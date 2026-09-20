"""Offline tests for Omni ChatCut and its implemented Music-to-MV workflow."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import HAS_FFMPEG, REPO_ROOT

import qwen_mm_plugins_omni_chatcut as music2mv
from qwen_mm_plugins_omni_chatcut.music_to_mv.image_providers import (
    QwenImage30Provider,
    SeedreamProvider,
    create_image_provider,
)
from qwen_mm_plugins_omni_chatcut.music_to_mv.video_providers import (
    SeedanceProvider,
    Wan30Provider,
    create_video_provider,
)

CAP_DIR = Path(REPO_ROOT) / "src" / "capabilities" / "omni-chatcut"
SKILL_DIR = CAP_DIR / "skill" / "music-to-mv"
EXPECTED_TOOLS = {"omni_call", "slice_audio_from_structure"}


def _load_script(relative: str):
    path = SKILL_DIR / relative
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_lists_atomic_music2mv_tools():
    # Omni ChatCut is a multi-domain server. Music-to-MV owns these tools but sibling domains may
    # register additional tools in the same capability.
    assert EXPECTED_TOOLS <= {item["name"] for item in music2mv.list_tools()}
    for item in music2mv.list_tools():
        assert item["inputSchema"]["type"] == "object"
        assert callable(music2mv.get_handler(item["name"]))
    assert music2mv.get_handler("missing") is None


def test_omni_call_dry_run_is_offline(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"small fake audio")
    blocks = music2mv.get_handler("omni_call")(
        {
            "audio_path": str(audio),
            "prompt": "Describe the structure.",
            "output_format": "json",
            "temperature": 0.0,
            "dry_run": True,
        }
    )
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"].startswith("DRY RUN")
    preview = json.loads(blocks[0]["text"].split("\n", 1)[1])
    assert preview["messages"][0]["content"][0]["type"] == "input_audio"
    assert preview["messages"][0]["content"][1]["text"] == "Describe the structure."
    assert preview["temperature"] == 0.0
    assert "api_key" not in preview


def test_omni_call_video_dry_run_exposes_video_sampling(tmp_path):
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"small fake video")
    blocks = music2mv.get_handler("omni_call")(
        {
            "video_path": str(video),
            "prompt": "Review this shot.",
            "output_format": "json",
            "fps": 3.0,
            "max_pixels": 200704,
            "dry_run": True,
        }
    )
    preview = json.loads(blocks[0]["text"].split("\n", 1)[1])
    media = preview["messages"][0]["content"][0]
    assert media["type"] == "video_url"
    assert media["fps"] == 3.0
    assert media["max_pixels"] == 200704


def test_omni_call_uses_shared_client(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv.tools import omni_call

    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"small fake audio")
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return "evidence", None

    monkeypatch.setattr(omni_call, "call_omni", fake_call)
    blocks = omni_call.handle(
        {
            "audio_path": str(audio),
            "prompt": "Analyze.",
            "api_key": "test-key",
            "base_url": "http://local/v1",
        }
    )
    assert blocks[0]["text"] == "evidence"
    assert captured["base_url"] == "http://local/v1"
    assert captured["messages"][0]["content"][0]["type"] == "input_audio"


def test_omni_call_reads_unified_model_config(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv.tools import omni_call

    audio = tmp_path / "song.mp3"
    config = tmp_path / "model-config.json"
    audio.write_bytes(b"small fake audio")
    config.write_text(
        json.dumps(
            {
                "omni": {
                    "base_url": "http://omni.example/v1",
                    "api_key_env": "OMNI_TEST_KEY",
                    "model": "omni-from-config",
                }
            }
        )
    )
    captured = {}
    monkeypatch.setattr(omni_call, "get_env", lambda name, default=None: {"OMNI_TEST_KEY": "secret"}.get(name, default))

    def fake_call(**kwargs):
        captured.update(kwargs)
        return "configured", None

    monkeypatch.setattr(omni_call, "call_omni", fake_call)
    result = omni_call.run_call(
        {
            "audio_path": str(audio),
            "prompt": "Analyze.",
            "model_config_path": str(config),
        }
    )
    assert result == "configured"
    assert captured["base_url"] == "http://omni.example/v1"
    assert captured["api_key"] == "secret"
    assert captured["model"] == "omni-from-config"


def test_model_config_rejects_inline_credentials(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv.model_config import load_model_config

    config = tmp_path / "model-config.json"
    config.write_text(json.dumps({"omni": {"api_key": "must-not-be-here"}}))
    with pytest.raises(ValueError, match="credential environment variables"):
        load_model_config(config)


def test_model_config_path_uses_shared_qwen_mm_setting(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import model_config

    config = tmp_path / "model-config.json"
    config.write_text("{}")
    monkeypatch.setattr(
        model_config,
        "get_env",
        lambda name, default=None, **_kwargs: str(config) if name == "QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG" else default,
    )

    assert model_config.resolve_model_config_path() == config.resolve()
    assert model_config.MODEL_CONFIG_ENV == "QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG"


def test_explicit_model_config_path_overrides_shared_setting(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import model_config

    configured = tmp_path / "configured.json"
    explicit = tmp_path / "explicit.json"
    monkeypatch.setattr(model_config, "get_env", lambda _name, default=None, **_kwargs: str(configured))

    assert model_config.resolve_model_config_path(explicit) == explicit.resolve()


def test_legacy_model_config_setting_is_not_supported(monkeypatch):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import model_config

    monkeypatch.setattr(
        model_config,
        "get_env",
        lambda name, default=None, **_kwargs: (
            "/legacy/model-config.json" if name == "OMNI_CHATCUT_MODEL_CONFIG" else default
        ),
    )

    assert model_config.resolve_model_config_path() is None


def test_shot_qc_normalizes_missing_reviews_conservatively():
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    segment = {
        "index": 4,
        "sub_shots": [{"global_index": 10}],
    }
    review = runner.normalize_qc_result({"shot_reviews": []}, segment)
    assert review["rating_counts"] == {
        "fully_compliant": 0,
        "minor_issues": 0,
        "major_issues": 1,
    }
    assert review["shot_reviews"][0]["issues"][0]["criterion"] == "review_completeness"
    assert review["segment_rating"] == "major_issues"
    assert review["issue_counts"] == {"major": 1, "minor": 0}
    assert "segment_score" not in review
    assert all("score" not in item for item in review["shot_reviews"])


def test_one_shot_request_contract_rejects_grouped_editorial_shots():
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    shot_0 = {
        "global_index": 0,
        "start_sec": 0,
        "end_sec": 2,
        "duration_sec": 2,
        "scene_id": "S1",
        "cast_present": [],
    }
    shot_1 = {
        "global_index": 1,
        "start_sec": 2,
        "end_sec": 4,
        "duration_sec": 2,
        "scene_id": "S1",
        "cast_present": [],
    }
    board = {
        "segments": [
            {
                "index": 0,
                "start_sec": 0,
                "end_sec": 4,
                "duration_sec": 4,
                "assembly_mode": "single_take_i2v",
                "scene_ids": ["S1"],
                "cast_present": [],
                "sub_shots": [shot_0, shot_1],
                "shot_windows": [
                    {"t": [0, 2], "sub_global_index": 0},
                    {"t": [2, 4], "sub_global_index": 1},
                ],
            }
        ]
    }
    errors = runner.one_shot_request_contract_errors(board)
    assert any("exactly one editorial shot" in error for error in errors)


def test_one_shot_request_contract_accepts_exact_shot_envelope():
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    shot = {
        "global_index": 7,
        "start_sec": 3,
        "end_sec": 7.25,
        "duration_sec": 4.25,
        "scene_id": "S2",
        "cast_present": ["C1"],
    }
    board = {
        "segments": [
            {
                "index": 0,
                "start_sec": 3,
                "end_sec": 7.25,
                "duration_sec": 4.25,
                "assembly_mode": "single_take_i2v",
                "scene_ids": ["S2"],
                "cast_present": ["C1"],
                "sub_shots": [shot],
                "shot_windows": [{"t": [0, 4.25], "sub_global_index": 7}],
            }
        ]
    }
    assert runner.one_shot_request_contract_errors(board) == []


def test_shot_qc_policy_and_fallback_ranking():
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    major = {
        "round": 1,
        "review": {
            "rating_counts": {"fully_compliant": 1, "minor_issues": 0, "major_issues": 1},
            "issue_counts": {"major": 1, "minor": 0},
        },
    }
    minor = {
        "round": 2,
        "review": {
            "rating_counts": {"fully_compliant": 0, "minor_issues": 2, "major_issues": 0},
            "issue_counts": {"major": 0, "minor": 2},
        },
    }
    assert runner.qc_candidate_accepted(major, "accept_all") is True
    assert runner.qc_candidate_accepted(major, "reject_major") is False
    assert runner.qc_candidate_accepted(minor, "reject_major") is True
    assert min([major, minor], key=runner.qc_candidate_sort_key) is minor


def test_pipeline_materializes_sentence_lyrics_from_canonical_evidence(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    project = tmp_path / "project"
    authoring = project / "authoring"
    caption_dir = project / "analysis/music-caption"
    authoring.mkdir(parents=True)
    caption_dir.mkdir(parents=True)
    board = authoring / "board.json"
    audio = project / "song.wav"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    (caption_dir / "evidence.json").write_text(
        json.dumps(
            {
                "sentence_lyrics": {
                    "status": "ok",
                    "cues": [
                        {
                            "index": 1,
                            "start_timestamp": "00:00:00,100",
                            "end_timestamp": "00:00:01,000",
                            "text_lines": ["First line"],
                        },
                        {
                            "index": 2,
                            "start_timestamp": "00:00:01,200",
                            "end_timestamp": "00:00:02,000",
                            "text_lines": ["Second line"],
                        },
                    ],
                }
            }
        )
    )
    pipeline = runner.Pipeline(board, audio, project / "execution")
    result = pipeline.materialize_lyrics_srt()
    assert result["status"] == "ready"
    assert result["source_kind"] == "music_caption_evidence"
    rendered = Path(result["srt_path"]).read_text()
    assert "00:00:00,100 --> 00:00:01,000" in rendered
    assert rendered.endswith("Second line\n")


def test_pipeline_burns_auto_discovered_evidence_into_final_mv(tmp_path, requires_ass_filter):
    if not HAS_FFMPEG:
        return
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    project = tmp_path / "project"
    authoring = project / "authoring"
    caption_dir = project / "analysis/music-caption"
    authoring.mkdir(parents=True)
    caption_dir.mkdir(parents=True)
    board = authoring / "board.json"
    audio = project / "song.wav"
    master = project / "master.mp4"
    final = project / "execution/final_mv.mp4"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    (caption_dir / "evidence.json").write_text(
        json.dumps(
            {
                "sentence_lyrics": {
                    "status": "ok",
                    "cues": [
                        {
                            "index": 1,
                            "start_timestamp": "00:00:00,100",
                            "end_timestamp": "00:00:01,000",
                            "text_lines": ["Pipeline subtitle"],
                        }
                    ],
                }
            }
        )
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=24:d=1.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.2",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(master),
        ],
        check=True,
    )
    pipeline = runner.Pipeline(board, audio, project / "execution")
    report = pipeline.render_final_subtitles(master, final)
    assert report["status"] == "burned"
    assert report["cue_count"] == 1
    assert final.is_file()
    assert (project / "execution/subtitles/lyrics.srt").is_file()
    assert (project / "execution/subtitles/lyrics.ass").is_file()
    assert (project / "execution/reports/subtitle_render.json").is_file()


def test_bundled_subtitle_renderer_burns_and_preserves_audio(tmp_path, requires_ass_filter):
    if not HAS_FFMPEG:
        return
    renderer = SKILL_DIR / "workflows/video-generation/scripts/burn_lyrics_subtitles.py"
    video = tmp_path / "input.mp4"
    srt = tmp_path / "lyrics.srt"
    output = tmp_path / "output.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=24:d=1.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.2",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
    )
    srt.write_text("1\n00:00:00,100 --> 00:00:01,000\nHello world\n")
    completed = subprocess.run(
        [sys.executable, str(renderer), "--video", str(video), "--srt", str(srt), "--output", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["validation"] == "passed"
    assert result["cue_count"] == 1
    assert output.is_file()
    assert output.with_suffix(".ass").is_file()
    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert {stream["codec_type"] for stream in probe["streams"]} == {"video", "audio"}


def test_shot_qc_keeps_three_rejected_candidates_and_selects_best(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    pipeline = object.__new__(runner.Pipeline)
    pipeline.output_dir = tmp_path
    pipeline.qc_config = {"strategy": "reject_major", "max_rounds": 3}
    pipeline.state = {
        "segments": {
            "0": {
                "status": "succeeded",
                "execution_signature": "video-signature",
                "output_signature": "video-signature",
                "output_path": str(tmp_path / "video_segments/raw/segment_000.mp4"),
                "task_id": "task-1",
            }
        }
    }
    raw = tmp_path / "video_segments/raw/segment_000.mp4"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"round-1")
    (tmp_path / "video_segments/candidates").mkdir(parents=True)
    (tmp_path / "reports/quality_control").mkdir(parents=True)
    pipeline.save_state = lambda: None
    pipeline.qc_prompt = lambda segment: "fixed prompt"
    major_issue_counts = iter([3, 1, 2])

    def fake_review(segment, candidate_path, round_index):
        issue_count = next(major_issue_counts)
        review = runner.normalize_qc_result(
            {
                "shot_reviews": [
                    {
                        "global_index": 0,
                        "rating": "major_issues",
                        "summary": f"round {round_index}",
                        "issues": [
                            {
                                "criterion": "action",
                                "severity": "major",
                                "expected": "Expected action",
                                "observed": f"Problem {item}",
                                "timestamp_sec": 1.0,
                            }
                            for item in range(issue_count)
                        ],
                    }
                ]
            },
            segment,
        )
        report = tmp_path / "reports/quality_control" / f"round-{round_index}.json"
        report.write_text("{}")
        return review, report

    def fake_run_videos(indexes, quality_control=True):
        round_number = pipeline.state["segments"]["0"]["quality_regeneration_count"] + 1
        raw.write_bytes(f"round-{round_number}".encode())
        pipeline.state["segments"]["0"].update(
            {
                "status": "succeeded",
                "output_path": str(raw),
                "output_signature": "video-signature",
                "task_id": f"task-{round_number}",
            }
        )

    pipeline.review_qc_candidate = fake_review
    pipeline.run_videos = fake_run_videos
    segment = {"index": 0, "duration_sec": 4, "sub_shots": [{"global_index": 0}]}
    result = runner.Pipeline.quality_control_segment(pipeline, segment)
    assert result["status"] == "fallback_selected"
    assert result["selected_round"] == 2
    assert len(result["rounds"]) == 3
    assert raw.read_bytes() == b"round-2"


def test_structure_parser_normalizes_omni_timestamp_drift():
    from qwen_mm_plugins_omni_chatcut.music_to_mv.slicing import parse_structure_asr

    structure = """[00:00,000 --> 00:019,840] [intro] ""
[00:019,840 --> 00:00:37,640] [verse] "Hello."
"""
    segments = parse_structure_asr(structure)
    assert segments[0]["end_sec"] == 19.84
    assert segments[0]["end_timestamp"] == "00:00:19,840"
    assert segments[1]["start_timestamp"] == "00:00:19,840"


def test_invalid_slice_request_is_returned_as_tool_error(tmp_path):
    blocks = music2mv.get_handler("slice_audio_from_structure")(
        {
            "audio_path": str(tmp_path / "missing.wav"),
            "structure_text": '[00:00,000 --> 00:01,000] [intro] ""',
            "output_dir": str(tmp_path / "clips"),
        }
    )
    assert blocks[0]["type"] == "text"
    assert "readable file" in blocks[0]["text"]


def test_slice_handler_writes_real_clips_and_manifest(tmp_path):
    if not HAS_FFMPEG:
        return
    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
    )
    blocks = music2mv.get_handler("slice_audio_from_structure")(
        {
            "audio_path": str(source),
            "structure_text": ('[00:00,000 --> 00:01,000] [intro] ""\n[00:01,000 --> 00:02,000] [verse] "Hello."'),
            "output_dir": str(tmp_path / "clips"),
        }
    )
    result = json.loads(blocks[0]["text"])
    assert result["segment_count"] == 2
    assert Path(result["manifest_path"]).is_file()
    assert all(Path(segment["clip_path"]).is_file() for segment in result["segments"])


def test_three_public_skills_with_music_to_mv_internal_workflows():
    skill_roots = sorted((CAP_DIR / "skill").glob("*/SKILL.md"))
    assert [path.parent.name for path in skill_roots] == [
        "movie-commentary",
        "music-to-mv",
        "video-translation",
    ]
    music_workflows = sorted((SKILL_DIR / "workflows").glob("*/WORKFLOW.md"))
    assert [path.parent.name for path in music_workflows] == [
        "music-caption",
        "storyboard",
        "video-generation",
    ]
    assert list((SKILL_DIR / "workflows").rglob("SKILL.md")) == []


def test_runtime_is_partitioned_by_skill_domain():
    runtime = CAP_DIR / "qwen_mm_plugins_omni_chatcut"
    assert [
        path.name
        for path in (
            runtime / "music_to_mv",
            runtime / "movie_commentary",
            runtime / "video_translation",
        )
        if path.is_dir()
    ] == ["music_to_mv", "movie_commentary", "video_translation"]
    assert (runtime / "music_to_mv/pipeline.py").is_file()
    assert (runtime / "music_to_mv/image_providers").is_dir()
    assert (runtime / "music_to_mv/video_providers").is_dir()
    assert not (runtime / "pipeline.py").exists()
    assert not (runtime / "providers").exists()
    assert all(
        (runtime / domain / "tools/__init__.py").is_file()
        for domain in ("music_to_mv", "movie_commentary", "video_translation")
    )


def test_runtime_registry_loads_all_skill_domains():
    assert music2mv.list_tools()


def test_profiles_and_manifest_use_workflows_not_child_skills():
    profiles = json.loads((SKILL_DIR / "assets/profiles.json").read_text())
    manifest = json.loads((SKILL_DIR / "assets" / "manifest-template.json").read_text())
    assert set(profiles) == {"standard", "merged-lyrics", "no-asset-qc"}
    assert all("asset_qc" not in profile for profile in profiles.values())
    assert profiles["no-asset-qc"] == {"extends": "standard"}
    assert set(manifest["workflows"]) == {"caption", "authoring", "execution"}
    assert {
        "music_caption",
        "music_caption_evidence",
        "music_caption_slice_manifest",
        "music_caption_run_state",
    }.issubset(manifest["artifacts"])
    assert "children" not in manifest
    assert "model_config" in manifest["inputs"]
    assert manifest["decisions"]["execution_authorization"]["status"].startswith("not_authorized")


def test_runner_reads_model_keys_through_shared_config(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    monkeypatch.setattr(runner, "get_env", lambda name, default=None: "configured-key")
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution")
    assert pipeline.image_api_key == "configured-key"
    assert pipeline.video_api_key == "configured-key"
    assert set(pipeline.state["assets"]) == {
        "characters",
        "provider_identities",
        "wardrobe",
        "keyframes",
    }


def test_pipeline_reads_unified_model_config(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    model_config = tmp_path / "model-config.json"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    model_config.write_text(
        json.dumps(
            {
                "omni": {
                    "base_url": "http://omni.example/v1",
                    "api_key_env": "OMNI_KEY",
                    "model": "configured-omni",
                },
                "image_providers": {
                    "qwen_image_3": {
                        "base_url": "http://image.example",
                        "api_key_env": "IMAGE_KEY",
                        "model": "configured-image",
                    }
                },
                "video_providers": {
                    "wan3": {
                        "base_url": "http://video.example",
                        "api_key_env": "VIDEO_KEY",
                        "model": "configured-video",
                    }
                },
            }
        )
    )
    values = {"IMAGE_KEY": "image-secret", "VIDEO_KEY": "video-secret", "OMNI_KEY": "omni-secret"}
    monkeypatch.setattr(runner, "get_env", lambda name, default=None: values.get(name, default))
    pipeline = runner.Pipeline(
        board,
        audio,
        tmp_path / "execution",
        model_config_path=model_config,
    )
    assert pipeline.image_provider.base_url == "http://image.example"
    assert pipeline.image_provider.model == "configured-image"
    assert pipeline.image_api_key == "image-secret"
    assert pipeline.video_provider.base_url == "http://video.example"
    assert pipeline.video_provider.model == "configured-video"
    assert pipeline.video_api_key == "video-secret"
    assert pipeline.qc_config["base_url"] == "http://omni.example/v1"
    assert pipeline.qc_config["api_key_env"] == "OMNI_KEY"
    assert pipeline.qc_config["model"] == "configured-omni"


def test_pipeline_plan_reports_semantic_qc_as_default_off(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution")
    policy = pipeline.plan()["semantic_quality_control"]
    assert policy == {
        "enabled": False,
        "source": "built_in_default",
        "strategy": None,
        "max_rounds": 0,
        "additional_omni_calls": False,
        "video_regeneration_possible": False,
    }


def test_pipeline_plan_reports_explicit_semantic_qc_opt_in(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    config = tmp_path / "run-config.json"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    config.write_text(json.dumps({"quality_control": {"enabled": True}}))
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution", config_path=config)
    policy = pipeline.plan()["semantic_quality_control"]
    assert policy["enabled"] is True
    assert policy["source"] == "run_config"
    assert policy["strategy"] == "reject_major"
    assert policy["max_rounds"] == 3
    assert policy["additional_omni_calls"] is True
    assert policy["video_regeneration_possible"] is True


def test_disabled_semantic_qc_skips_without_review_calls(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    pipeline = object.__new__(runner.Pipeline)
    pipeline.output_dir = tmp_path
    pipeline.qc_config = {"enabled": False}
    pipeline.review_qc_candidate = lambda *args, **kwargs: pytest.fail("disabled QC must not call Omni")
    report_path, report = runner.Pipeline.run_quality_control(pipeline)
    assert report == {"enabled": False, "status": "skipped", "segments": []}
    assert json.loads(report_path.read_text()) == report


def test_base_assets_generate_only_cast_identity_portraits(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    board.write_text(
        json.dumps(
            {
                "creative_direction": {},
                "style_bible": {},
                "cast": [
                    {
                        "id": "C1",
                        "role": "lead",
                        "identity": "fictional adult lead",
                        "portrait_t2i_prompt": "adult lead identity portrait",
                    }
                ],
                "scenes": [
                    {
                        "id": "S1",
                        "name": "rooftop",
                        "setting": "a rooftop whose composition changes by shot",
                        "lighting": "blue hour",
                        "palette": "indigo and amber",
                        "wardrobe": {"C1": "dark coat"},
                    }
                ],
                "segments": [
                    {
                        "index": 0,
                        "start_sec": 0,
                        "end_sec": 4,
                        "duration_sec": 4,
                        "scene_ids": ["S1"],
                        "cast_present": ["C1"],
                        "assembly_mode": "single_take_i2v",
                        "sub_shots": [
                            {
                                "global_index": 0,
                                "start_sec": 0,
                                "end_sec": 4,
                                "duration_sec": 4,
                                "scene_id": "S1",
                                "cast_present": ["C1"],
                            }
                        ],
                        "shot_windows": [{"t": [0, 4], "sub_global_index": 0}],
                    }
                ],
            }
        )
    )
    audio.write_bytes(b"fake")
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution")
    calls = []
    pipeline.generate_image = lambda category, asset_id, *args, **kwargs: calls.append((category, asset_id))

    pipeline.generate_base_assets()

    assert calls == [("characters", "C1")]
    assert not (pipeline.output_dir / "generated/scenes").exists()


def test_qwen_image30_adapter_maps_text_and_reference_payload():
    provider = QwenImage30Provider(
        {
            "base_url": "https://trial.cn-beijing.maas.aliyuncs.com",
            "api_key_env": "DASHSCOPE_API_KEY",
            "model": "qwen-image-3.0-pro",
            "size": "2048x1152",
            "prompt_extend": False,
            "prompt_extend_mode": "direct",
            "enable_thinking": False,
            "negative_prompt": "text, watermark",
            "seed": 7,
        }
    )
    payload = provider.build_payload("cinematic portrait", ["https://example/reference.png"], {})
    assert payload["input"]["messages"][0]["content"] == [
        {"image": "https://example/reference.png"},
        {"text": "cinematic portrait"},
    ]
    assert payload["parameters"] == {
        "prompt_extend": False,
        "prompt_extend_mode": "direct",
        "enable_thinking": False,
        "n": 1,
        "size": "2048*1152",
        "watermark": False,
        "negative_prompt": "text, watermark",
        "seed": 7,
    }
    assert provider.submit_url().endswith("/api/v1/services/aigc/multimodal-generation/generation")
    response = {"output": {"choices": [{"message": {"content": [{"image": "https://example/out.png"}]}}]}}
    assert provider.image_urls(response) == ["https://example/out.png"]
    assert provider.output_path(Path("scene.jpg")) == Path("scene.png")


def test_qwen_image30_adapter_honors_category_overrides():
    provider = QwenImage30Provider(
        {
            "base_url": "https://trial.cn-beijing.maas.aliyuncs.com",
            "api_key_env": "DASHSCOPE_API_KEY",
            "model": "qwen-image-3.0-pro",
            "size": "2048*1152",
            "negative_prompt": "generic exclusions",
        }
    )
    image_config = {
        "size_override": "1536*1536",
        "negative_prompt_override": "prominent foreground person, portrait",
    }
    payload = provider.build_payload("reusable environment plate", [], image_config)
    assert payload["parameters"]["size"] == "1536*1536"
    assert payload["parameters"]["negative_prompt"] == "prominent foreground person, portrait"
    assert provider.signature_parameters(image_config)["negative_prompt"] == payload["parameters"]["negative_prompt"]


def test_qwen_image30_adapter_rejects_unsupported_requests():
    common = {
        "base_url": "https://trial.cn-beijing.maas.aliyuncs.com",
        "api_key_env": "DASHSCOPE_API_KEY",
        "model": "qwen-image-3.0-pro",
        "size": "2048*1152",
    }
    with pytest.raises(ValueError, match="at most 3"):
        QwenImage30Provider(common).build_payload("prompt", [f"https://example/{index}.png" for index in range(4)], {})
    with pytest.raises(ValueError, match="text-to-image only"):
        QwenImage30Provider({**common, "prompt_extend_mode": "agent"}).build_payload(
            "prompt", ["https://example/ref.png"], {}
        )
    with pytest.raises(ValueError, match="total pixels"):
        QwenImage30Provider({**common, "size": "4096*4096"}).build_payload("prompt", [], {})


def test_seedream_adapter_maps_identity_payload_and_results():
    provider = SeedreamProvider(
        {
            "base_url": "https://ark.cn-beijing.volces.com",
            "api_key_env": "ARK_API_KEY",
            "model": "doubao-seedream-4-5-251128",
            "size": "2K",
            "watermark": False,
            "seed": 9,
        }
    )
    payload = provider.build_payload("fictional adult identity portrait", [], {})
    assert payload == {
        "model": "doubao-seedream-4-5-251128",
        "prompt": "fictional adult identity portrait",
        "response_format": "url",
        "size": "2K",
        "stream": False,
        "watermark": False,
        "seed": 9,
    }
    assert provider.submit_url().endswith("/api/v3/images/generations")
    assert provider.image_urls({"data": [{"url": "https://example/out.jpg"}]}) == ["https://example/out.jpg"]
    assert provider.output_path(Path("identity.png")) == Path("identity.jpg")
    with pytest.raises(ValueError, match="text prompts only"):
        provider.build_payload("portrait", ["https://example/reference.jpg"], {})


def test_image_provider_factory_recognizes_supported_aliases():
    common = {"base_url": "https://example", "api_key_env": "KEY", "model": "model", "size": "1024*1024"}
    qwen = create_image_provider("qwen-image-3.0-pro", common)
    assert qwen.name == "qwen_image_3"
    assert create_image_provider("qwen_image_3", common).name == "qwen_image_3"
    assert create_image_provider("seedream-4.5", common).name == "seedream"
    assert create_image_provider("doubao-seedream-4-5-251128", common).name == "seedream"
    with pytest.raises(ValueError, match="unsupported image.provider"):
        create_image_provider("unknown", common)


def test_wan30_adapter_maps_payload_headers_and_results():
    provider = Wan30Provider(
        {
            "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com",
            "api_key_env": "DASHSCOPE_API_KEY",
            "model": "wan3.0-video",
            "prompt_extend": False,
            "audio": False,
            "watermark": False,
            "seed": 7,
        }
    )
    record = {"video_prompt": "图1是人物，音频1控制动作", "api_duration_sec": 10}
    payload = provider.build_payload(
        record,
        ["data:image/jpeg;base64,person"],
        "oss://dashscope-instant/audio.wav",
        {"resolution": "720p", "ratio": None},
    )
    assert payload["input"]["media"] == [
        {"type": "reference_image", "url": "data:image/jpeg;base64,person"},
        {"type": "reference_audio", "url": "oss://dashscope-instant/audio.wav"},
    ]
    assert payload["parameters"] == {
        "resolution": "720P",
        "ratio": "adaptive",
        "duration": 10,
        "audio": False,
        "prompt_extend": False,
        "watermark": False,
        "seed": 7,
    }
    assert provider.submit_headers(payload) == {
        "X-DashScope-Async": "enable",
        "X-DashScope-OssResourceResolve": "enable",
    }
    response = {"output": {"task_id": "wan-task", "task_status": "SUCCEEDED", "video_url": "https://v"}}
    assert provider.task_id(response) == "wan-task"
    assert provider.remote_status(response) == "succeeded"
    assert provider.video_url(response) == "https://v"


def test_wan30_adapter_rejects_inline_audio_and_reference_overflow():
    provider = Wan30Provider(
        {
            "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com",
            "api_key_env": "DASHSCOPE_API_KEY",
            "model": "wan3.0-video",
        }
    )
    record = {"video_prompt": "prompt", "api_duration_sec": 10}
    with pytest.raises(ValueError, match="reference_audio"):
        provider.build_payload(
            record,
            [],
            "data:audio/wav;base64,audio",
            {"resolution": "720P", "ratio": "16:9"},
        )
    with pytest.raises(ValueError, match="at most 10"):
        provider.build_payload(
            record,
            [f"https://example/{index}.jpg" for index in range(11)],
            "https://example/audio.wav",
            {"resolution": "720P", "ratio": "16:9"},
        )


def test_video_provider_factory_recognizes_wan_aliases():
    common = {"base_url": "https://example", "api_key_env": "KEY", "model": "model"}
    wan = create_video_provider("wan3.0", common)
    assert wan.name == "wan3"
    assert create_video_provider("wan30", common).name == "wan3"
    with pytest.raises(ValueError, match="unsupported video.provider"):
        create_video_provider("unknown", common)


def test_seedance_adapter_maps_payload_results_and_redacts_inline_media():
    provider = SeedanceProvider(
        {
            "base_url": "https://ark.cn-beijing.volces.com",
            "api_key_env": "ARK_API_KEY",
            "model": "doubao-seedance-2-5-260628",
            "generate_audio": False,
            "watermark": False,
        }
    )
    record = {"video_prompt": "one continuous cinematic shot", "api_duration_sec": 8}
    payload = provider.build_payload(
        record,
        ["asset://asset-official-preset"],
        "data:audio/wav;base64,audio",
        {"resolution": "1080P", "ratio": "adaptive"},
    )
    assert payload == {
        "model": "doubao-seedance-2-5-260628",
        "content": [
            {"type": "text", "text": "one continuous cinematic shot"},
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {"url": "asset://asset-official-preset"},
            },
            {
                "type": "audio_url",
                "role": "reference_audio",
                "audio_url": {"url": "data:audio/wav;base64,audio"},
            },
        ],
        "generate_audio": False,
        "resolution": "1080p",
        "ratio": "16:9",
        "duration": 8,
        "watermark": False,
    }
    saved = provider.persisted_payload(payload)
    assert saved["content"][1]["image_url"]["url"] == "asset://asset-official-preset"
    assert saved["content"][2]["audio_url"]["url"].startswith("@inline:audio/wav;base64;sha256=")
    assert "base64,audio" not in json.dumps(saved)
    assert provider.submit_url().endswith("/api/v3/contents/generations/tasks")
    response = {"id": "seed-task", "status": "succeeded", "content": {"video_url": "https://v"}}
    assert provider.task_id(response) == "seed-task"
    assert provider.remote_status(response) == "succeeded"
    assert provider.video_url(response) == "https://v"


def test_seedance_adapter_clamps_13_second_storyboard_shots_to_12_seconds():
    provider = SeedanceProvider({"base_url": "https://example", "api_key_env": "ARK_API_KEY", "model": "seedance"})
    with pytest.raises(ValueError, match="4 through 12"):
        provider.build_payload(
            {"video_prompt": "shot", "api_duration_sec": 13},
            [],
            "data:audio/wav;base64,audio",
            {"resolution": "1080p", "ratio": "16:9"},
        )
    assert provider.api_duration(12.1) == 12
    assert provider.api_duration(12.999) == 12
    assert provider.api_duration(2.827) == 4
    assert provider.storyboard_errors([{"index": 7, "duration_sec": 12.1}]) == []
    errors = provider.storyboard_errors([{"index": 7, "duration_sec": 13.1}])
    assert errors == ["shot 7: storyboard duration 13.1s exceeds the supported 13s normalization ceiling"]


def test_seedance_adapter_uses_only_explicit_official_preset_binding():
    provider = SeedanceProvider(
        {
            "base_url": "https://ark.cn-beijing.volces.com",
            "api_key_env": "ARK_API_KEY",
            "model": "seedance",
            "official_identity_assets": {"C1": "asset://asset-official-preset"},
        }
    )
    reference = {
        "cast_id": "C1",
        "source_url": "https://temporary/identity.png",
        "asset_uri": "asset://asset-old-private",
    }
    assert (
        provider.identity_reference_url(reference, "data:image/png;base64,ignored") == "asset://asset-official-preset"
    )
    with pytest.raises(ValueError, match="Ark image asset"):
        provider.identity_reference_url({**reference, "cast_id": "C2"}, "data:image/png;base64,unsupported")


def test_video_provider_factory_recognizes_seedance_aliases():
    common = {"base_url": "https://example", "api_key_env": "KEY", "model": "model"}
    assert create_video_provider("seedance", common).name == "seedance"
    assert create_video_provider("doubao-seedance-2.5", common).name == "seedance"


def test_seedance_inline_audio_transport_keeps_base64_out_of_state(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    audio = tmp_path / "segment.wav"
    audio.write_bytes(b"wav")
    pipeline = object.__new__(runner.Pipeline)
    pipeline.video_provider = SeedanceProvider(
        {"base_url": "https://example", "api_key_env": "ARK_API_KEY", "model": "seedance"}
    )
    pipeline.state = {"segments": {}}
    pipeline.save_state = lambda: None
    record = {"segment_index": 1, "audio_path": str(audio)}
    pipeline.state["segments"]["1"] = record
    record_path = tmp_path / "audio.json"
    url = pipeline.reference_audio_url(record, record_path)
    assert url.startswith("data:audio/wav;base64,")
    assert "url" not in record["reference_audio"]
    assert "base64" not in record_path.read_text()


def test_seedance_submit_uses_live_inline_audio_and_persists_redacted_request(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    provider = SeedanceProvider(
        {
            "base_url": "https://example",
            "api_key_env": "ARK_API_KEY",
            "model": "seedance",
            "official_identity_assets": {"C1": "asset://asset-official-preset"},
        }
    )
    record = {
        "segment_index": 0,
        "duration_sec": 8.0,
        "api_duration_sec": 8,
        "video_prompt": "one continuous shot",
        "identity_assets": [
            {
                "cast_id": "C1",
                "source_url": "https://example/identity.png",
                "source_hash": "identity-hash",
            }
        ],
        "execution_signature": "signature",
        "status": "prepared",
    }
    pipeline = object.__new__(runner.Pipeline)
    pipeline.video_provider = provider
    pipeline.video_api_key = "private-key"
    pipeline.video_config = {
        "resolution": "1080p",
        "ratio": "16:9",
        "max_submit_attempts": 1,
    }
    pipeline.output_dir = tmp_path
    pipeline.state = {"segments": {"0": record}}
    pipeline.prepare_segment = lambda segment: record
    pipeline.validate_local_identity = lambda reference: None
    pipeline.reference_audio_url = lambda current, path: "data:audio/wav;base64,private-audio"
    pipeline.identity_reference_url = lambda reference: provider.identity_reference_url(reference, None)
    pipeline.relative = lambda path: str(path)
    pipeline.save_state = lambda: None
    captured = {}

    def fake_request(method, url, payload, **kwargs):
        captured.update({"method": method, "url": url, "payload": payload, "kwargs": kwargs})
        return 201, {"id": "seed-task"}, {}

    pipeline.request = fake_request
    result = pipeline.submit_segment({"index": 0})
    saved = json.loads((tmp_path / "requests/videos/segment_000.json").read_text())
    assert captured["payload"]["content"][-1]["audio_url"]["url"].endswith("private-audio")
    assert saved["content"][-1]["audio_url"]["url"].startswith("@inline:audio/wav;base64;sha256=")
    assert "private-audio" not in json.dumps(saved)
    assert result["status"] == "submitted"
    assert result["task_id"] == "seed-task"


@pytest.mark.parametrize("use_model_template", [False, True], ids=["built-in", "model-template"])
def test_pipeline_defaults_work_without_workspace(monkeypatch, tmp_path, use_model_template):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import model_config
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")

    def fake_get_env(name, default=None, **_kwargs):
        if "WORKSPACE" in name:
            pytest.fail("Shared-domain defaults must not look up a workspace ID")
        return "test-key" if name == "DASHSCOPE_API_KEY" else default

    monkeypatch.setattr(runner, "get_env", fake_get_env)
    monkeypatch.setattr(model_config, "get_env", fake_get_env)
    pipeline = runner.Pipeline(
        board,
        audio,
        tmp_path / "execution",
        model_config_path=SKILL_DIR / "assets/model-config.example.json" if use_model_template else None,
    )
    pipeline.image_provider.validate_configuration()
    pipeline.video_provider.validate_configuration()
    assert pipeline.image_api_key == pipeline.video_api_key == "test-key"
    assert pipeline.image_provider.submit_url() == (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert pipeline.video_provider.submit_url() == (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    )
    assert pipeline.video_provider.query_url("test-task") == "https://dashscope.aliyuncs.com/api/v1/tasks/test-task"


def test_pipeline_resolves_qwen_and_wan_workspaces(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    config = tmp_path / "config.json"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    config.write_text(
        json.dumps(
            {
                "image_providers": {
                    "qwen_image_3": {
                        "base_url": "https://{workspace_id}.cn-beijing.maas.aliyuncs.com",
                        "workspace_id_env": "WORKSPACE_ID",
                        "api_key_env": "IMAGE_KEY",
                        "model": "qwen-image-3.0-pro",
                    }
                },
                "video": {"provider": "wan3", "resolution": "720p"},
                "providers": {
                    "wan3": {
                        "base_url": "https://{workspace_id}.cn-beijing.maas.aliyuncs.com",
                        "workspace_id_env": "WORKSPACE_ID",
                        "api_key_env": "WAN_KEY",
                        "model": "wan3.0-video",
                    }
                },
            }
        )
    )
    values = {"IMAGE_KEY": "image-key", "WAN_KEY": "video-key", "WORKSPACE_ID": "ws-123"}
    monkeypatch.setattr(runner, "get_env", lambda name, default=None: values.get(name, default))
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution", config)
    assert pipeline.image_api_key == "image-key"
    assert pipeline.video_api_key == "video-key"
    assert pipeline.video_provider.name == "wan3"
    assert pipeline.video_provider.base_url == "https://ws-123.cn-beijing.maas.aliyuncs.com"
    assert pipeline.video_config["submit_rpm_steps"] == [5, 3, 2, 1]
    assert pipeline.state["models"] == {
        "image": "qwen-image-3.0-pro",
        "video": "wan3.0-video",
        "quality_control": None,
    }


def test_pipeline_generates_qwen_image_and_downloads_png(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    config = tmp_path / "config.json"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    config.write_text(
        json.dumps(
            {
                "image": {"provider": "qwen_image_3"},
                "image_providers": {
                    "qwen_image_3": {
                        "base_url": "https://{workspace_id}.cn-beijing.maas.aliyuncs.com",
                        "workspace_id_env": "WORKSPACE_ID",
                        "api_key_env": "QWEN_IMAGE_KEY",
                        "model": "qwen-image-3.0-pro",
                        "size": "2048*1152",
                    }
                },
            }
        )
    )
    values = {"QWEN_IMAGE_KEY": "qwen-key", "DASHSCOPE_API_KEY": "wan-key", "WORKSPACE_ID": "ws-123"}
    monkeypatch.setattr(runner, "get_env", lambda name, default=None: values.get(name, default))
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution", config)
    assert pipeline.image_provider.name == "qwen_image_3"
    assert pipeline.image_provider.base_url == "https://ws-123.cn-beijing.maas.aliyuncs.com"
    assert pipeline.image_api_key == "qwen-key"
    assert pipeline.video_provider.name == "wan3"
    assert pipeline.video_api_key == "wan-key"

    calls = []

    def fake_request(method, url, payload=None, **kwargs):
        calls.append({"method": method, "url": url, "payload": payload, **kwargs})
        return (
            200,
            {"output": {"choices": [{"message": {"content": [{"image": "https://example/out.png"}]}}]}},
            {},
        )

    def fake_download(url, output_path):
        Path(output_path).write_bytes(b"png")

    pipeline.request = fake_request
    pipeline.download = fake_download
    record = pipeline.generate_image(
        "characters",
        "C1",
        "cinematic identity portrait",
        pipeline.output_dir / "generated/characters/C1_identity.jpg",
        ["cast[C1].portrait_t2i_prompt"],
    )
    assert record["provider"] == "qwen_image_3"
    assert record["output_path"].endswith("C1_identity.png")
    assert Path(record["output_path"]).read_bytes() == b"png"
    assert calls[0]["api_key"] == "qwen-key"
    assert calls[0]["url"].endswith("/api/v1/services/aigc/multimodal-generation/generation")
    assert calls[0]["payload"]["input"]["messages"][0]["content"] == [{"text": "cinematic identity portrait"}]
    assert (
        pipeline.generate_image(
            "characters",
            "C1",
            "cinematic identity portrait",
            pipeline.output_dir / "generated/characters/C1_identity.jpg",
            ["cast[C1].portrait_t2i_prompt"],
        )
        == record
    )
    assert len(calls) == 1


def test_pipeline_generates_seedream_identity_and_downloads_jpg(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    config = tmp_path / "config.json"
    board.write_text(json.dumps({"creative_direction": {}, "cast": [], "scenes": [], "segments": []}))
    audio.write_bytes(b"fake")
    config.write_text(
        json.dumps(
            {
                "image": {"provider": "seedream"},
                "image_providers": {
                    "seedream": {
                        "base_url": "https://ark.cn-beijing.volces.com",
                        "api_key_env": "ARK_API_KEY",
                        "model": "doubao-seedream-4-5-251128",
                        "character_size": "2K",
                    }
                },
            }
        )
    )
    values = {"ARK_API_KEY": "seed-key", "DASHSCOPE_API_KEY": "wan-key"}
    monkeypatch.setattr(runner, "get_env", lambda name, default=None: values.get(name, default))
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution", config)
    assert pipeline.image_provider.name == "seedream"
    assert pipeline.image_api_key == "seed-key"

    calls = []

    def fake_request(method, url, payload=None, **kwargs):
        calls.append({"method": method, "url": url, "payload": payload, **kwargs})
        return 200, {"data": [{"url": "https://example/out.jpg"}]}, {}

    pipeline.request = fake_request
    pipeline.download = lambda url, output_path: Path(output_path).write_bytes(b"jpg")
    record = pipeline.generate_image(
        "characters",
        "C1",
        "fictional adult identity portrait",
        pipeline.output_dir / "generated/characters/C1_identity.jpg",
        ["cast[C1].portrait_t2i_prompt"],
    )
    assert record["provider"] == "seedream"
    assert record["output_path"].endswith("C1_identity.jpg")
    assert calls[0]["api_key"] == "seed-key"
    assert calls[0]["url"].endswith("/api/v3/images/generations")
    assert calls[0]["payload"]["prompt"] == "fictional adult identity portrait"


def test_wan_audio_upload_keeps_key_out_of_command(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    audio = tmp_path / "segment.wav"
    audio.write_bytes(b"wav")
    pipeline = object.__new__(runner.Pipeline)
    pipeline.video_provider = Wan30Provider(
        {
            "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com",
            "api_key_env": "WAN_KEY",
            "model": "wan3.0-video",
            "reference_audio_mode": "dashscope_oss",
        }
    )
    pipeline.video_api_key = "private-test-key"
    pipeline.state = {"segments": {}}
    pipeline.save_state = lambda: None
    captured = {}
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/dashscope")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SimpleNamespace(stdout="Uploaded oss url: oss://dashscope-instant/audio.wav\n")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    record = {"segment_index": 1, "audio_path": str(audio)}
    pipeline.state["segments"]["1"] = record
    url = pipeline.wan_reference_audio_url(record, tmp_path / "audio.json")
    assert url == "oss://dashscope-instant/audio.wav"
    assert "private-test-key" not in captured["command"]
    assert captured["environment"]["DASHSCOPE_API_KEY"] == "private-test-key"


def test_state_inspector_routes_valid_authoring_to_execution(tmp_path):
    storyboard = {
        "video": {"duration_sec": 5},
        "creative_direction": {"media_form": "live_action"},
        "style_bible": {},
        "cast": [],
        "scenes": [],
        "segments": [
            {
                "assembly_mode": "single_take_i2v",
                "sub_shots": [{"global_index": 0}],
                "shot_windows": [{"t": [0, 5], "sub_global_index": 0}],
            }
        ],
    }
    (tmp_path / "storyboard.json").write_text(json.dumps(storyboard))
    (tmp_path / "validation_report.json").write_text(json.dumps({"valid": True, "quality_pass": True, "metrics": {}}))
    (tmp_path / "continuity_report.json").write_text(
        json.dumps({"schema": "author-mv/continuity-report/v1", "pass": True})
    )
    (tmp_path / "source_audio.mp3").write_bytes(b"audio")
    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts/inspect_music2mv_state.py"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    state = json.loads(completed.stdout)
    assert state["recommended_stage"] == "execute_ready"
    assert state["blockers"] == []


def test_state_inspector_resumes_project_backed_caption_run(tmp_path):
    audio = tmp_path / "inputs" / "source_audio.mp3"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")
    run_dir = tmp_path / "analysis" / "music-caption" / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    (run_dir / "run-state.json").write_text(
        json.dumps(
            {
                "schema": "music2mv/music-caption-run/v1",
                "run_id": "run-001",
                "run_dir": "analysis/music-caption/runs/run-001",
                "work_dir": "work/music-caption/run-001",
                "source_audio": "inputs/source_audio.mp3",
                "status": "running",
                "phase": "first_wave_complete",
                "branches": {},
                "segments": {},
                "last_error": "",
            }
        )
    )
    clip = tmp_path / "work" / "music-caption" / "run-001" / "clips" / "segment_001.wav"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"clip")
    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts/inspect_music2mv_state.py"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    state = json.loads(completed.stdout)
    assert state["recommended_stage"] == "caption_resume"
    assert state["caption_run"]["phase"] == "first_wave_complete"
    assert state["caption_run"]["source_audio_readable"] is True
    assert state["primary_artifacts"]["audio"] == str(audio)


def test_workflow_scripts_have_working_help():
    scripts = [
        "workflows/storyboard/scripts/normalize_music_analysis.py",
        "workflows/storyboard/scripts/validate_storyboard.py",
        "workflows/video-generation/scripts/validate_execution_storyboard.py",
        "workflows/video-generation/scripts/run_mv_pipeline.py",
        "scripts/inspect_music2mv_state.py",
    ]
    for relative in scripts:
        completed = subprocess.run(
            [sys.executable, str(SKILL_DIR / relative), "--help"],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, f"{relative}: {completed.stderr}"


def test_storyboard_validator_accepts_whole_film_treatment_and_merged_visual_unit(tmp_path):
    board = {
        "video": {"duration_sec": 4},
        "creative_direction": {
            "media_form": "live_action",
            "whole_film_treatment": {
                "core_premise": "A guarded person gradually permits one honest gesture.",
                "driving_thread": "Distance becomes direct engagement through visible behavior.",
                "visual_world_logic": "Changing framings and environments share tactile night photography.",
                "recurring_motifs": [],
                "development_path": [
                    {
                        "id": "stage-1",
                        "start_sec": 0,
                        "end_sec": 4,
                        "visible_state": "The figure avoids the camera.",
                        "development": "The figure turns and opens one hand.",
                        "handoff": "The open gesture completes the short film.",
                    }
                ],
            },
            "visual_units": [
                {
                    "id": "VU-001",
                    "start_sec": 0,
                    "end_sec": 4,
                    "source_kind": "merged_cues",
                    "development_stage_id": "stage-1",
                    "lyric_cues": [
                        {"index": 1, "start_sec": 0, "end_sec": 2, "text": "First line"},
                        {"index": 2, "start_sec": 2, "end_sec": 4, "text": "Second line"},
                    ],
                    "structure_refs": ["verse"],
                    "section_caption_evidence": [
                        {
                            "structure_index": 0,
                            "label": "verse",
                            "start_sec": 0,
                            "end_sec": 4,
                            "overlap_start_sec": 0,
                            "overlap_end_sec": 4,
                            "caption": "A restrained verse gradually broadens through warmer harmony.",
                            "design_application": "Keep the turn restrained, then open the hand as the harmony broadens.",
                        }
                    ],
                    "visual_intent": "Complete one continuous turn from avoidance to engagement.",
                    "grouping_reason": "The adjacent short phrases form one uninterrupted thought.",
                }
            ],
            "editorial_plan": {"long_take_threshold_sec": 8},
        },
        "style_bible": {
            "overall_visual_style": "tactile live-action night photography",
            "color_palette": "indigo and warm amber",
            "film_look": "natural grain",
            "mood": "guarded but warming",
            "composition_grammar": "layered depth",
            "camera_grammar": "measured camera movement",
            "lighting_grammar": "motivated practical light",
            "material_language": "worn concrete and cotton",
            "production_design": "sparse lived-in details",
            "wardrobe_rules": "stable dark clothing",
            "recurring_elements": [],
            "prohibited_elements": [],
        },
        "cast": [],
        "scenes": [
            {
                "id": "S1",
                "name": "night passage",
                "setting": "a deep covered passage",
                "lighting": "warm practical light against blue night",
                "palette": "indigo and amber",
                "wardrobe": {},
            }
        ],
        "segments": [
            {
                "index": 0,
                "start_sec": 0,
                "end_sec": 4,
                "duration_sec": 4,
                "assembly_mode": "single_take_i2v",
                "scene_ids": ["S1"],
                "cast_present": [],
                "sub_shots": [
                    {
                        "seg_index": 0,
                        "sub_index": 0,
                        "global_index": 0,
                        "start_sec": 0,
                        "end_sec": 4,
                        "duration_sec": 4,
                        "visual_unit_id": "VU-001",
                        "shot_type": "concept",
                        "shot_function": "turn avoidance into engagement",
                        "shot_summary": "A silhouetted figure turns and opens one hand toward camera.",
                        "scene_id": "S1",
                        "cast_present": [],
                        "visual_design": {
                            "image_content": "one anonymous silhouette in the passage",
                            "composition": "deep centered perspective",
                            "lighting_color_material": "amber practicals, indigo shadows, worn concrete",
                            "visible_change": "the body turns and the hand opens",
                            "handoff_to_next": "the gesture settles",
                        },
                        "action_design": {
                            "playable_verb": "turn and open",
                            "start_physical_state": "body facing away",
                            "action_steps": ["turn shoulders", "raise forearm", "open hand"],
                            "end_physical_state": "open hand facing camera",
                        },
                        "camera": {
                            "shot_size": "medium wide",
                            "angle": "eye level",
                            "movement": "slow push",
                            "subject_vs_camera": "camera approaches while subject turns",
                        },
                        "camera_geometry": {"focus_subject": "the opening hand"},
                        "audio_sync": {
                            "vocal_present": True,
                            "lyric": "First line / Second line",
                            "edit_anchor": {
                                "source": "track_start",
                                "source_time_sec": 0,
                                "evidence": "track and first lyric begin",
                                "selection_reason": "opens the only visual development",
                            },
                        },
                        "reference_image": {"cast_scene_table": {"characters": []}},
                        "continuity": {"link_mode": "start"},
                        "transition_in": "fade_in",
                    }
                ],
                "shot_windows": [{"t": [0, 4], "sub_global_index": 0, "what": "the full turn"}],
            }
        ],
    }
    storyboard = tmp_path / "storyboard.json"
    storyboard.write_text(json.dumps(board))
    music_map = tmp_path / "music_map.json"
    music_map.write_text(
        json.dumps(
            {
                "structure": [
                    {
                        "start_sec": 0,
                        "end_sec": 4,
                        "label": "verse",
                        "caption": "A restrained verse gradually broadens through warmer harmony.",
                    }
                ]
            }
        )
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "workflows/storyboard/scripts/validate_storyboard.py"),
            str(storyboard),
            "--music-map",
            str(music_map),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert report["metrics"]["development_stage_count"] == 1
    assert report["metrics"]["visual_unit_count"] == 1
    assert report["metrics"]["merged_visual_unit_count"] == 1

    board["creative_direction"]["visual_units"][0]["section_caption_evidence"][0]["caption"] = "Rewritten caption"
    storyboard.write_text(json.dumps(board))
    rejected = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "workflows/storyboard/scripts/validate_storyboard.py"),
            str(storyboard),
            "--music-map",
            str(music_map),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 1
    rejected_report = json.loads(rejected.stdout)
    assert any(item["code"] == "section_caption_source" for item in rejected_report["errors"])


def test_video_prompt_context_uses_whole_film_and_visual_unit_direction():
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    pipeline = object.__new__(runner.Pipeline)
    pipeline.board = {
        "creative_direction": {
            "whole_film_treatment": {
                "core_premise": "Distance gradually becomes trust.",
                "driving_thread": "The lead permits increasingly direct contact.",
                "visual_world_logic": "Different places share tactile nocturnal photography.",
                "recurring_motifs": [],
            }
        }
    }
    pipeline.visual_unit_map = {
        "VU-001": {
            "development_stage_id": "stage-1",
            "visual_intent": "Complete one hesitant approach.",
            "section_caption_evidence": [
                {
                    "caption": "Raw upstream arrangement and vocal description.",
                    "design_application": "Authoring-only application note.",
                }
            ],
        }
    }
    pipeline.development_stage_map = {
        "stage-1": {"visible_state": "The lead remains distant.", "development": "The lead approaches."}
    }

    context = pipeline.creative_context_text({"visual_unit_id": "VU-001"})

    assert "Distance gradually becomes trust" in context
    assert "The lead approaches" in context
    assert "Complete one hesitant approach" in context
    assert "Raw upstream arrangement" not in context
    assert "Authoring-only application" not in context


@pytest.fixture
def official_seedance_pipeline(tmp_path):
    from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline as runner

    board = tmp_path / "board.json"
    audio = tmp_path / "song.wav"
    board.write_text(
        json.dumps(
            {
                "creative_direction": {},
                "style_bible": {},
                "cast": [
                    {
                        "id": "C1",
                        "role": "lead",
                        "identity": "fictional adult lead",
                        "portrait_t2i_prompt": "adult lead identity portrait",
                    }
                ],
                "scenes": [
                    {
                        "id": "S1",
                        "name": "rooftop",
                        "setting": "a rooftop whose composition changes by shot",
                        "lighting": "blue hour",
                        "palette": "indigo and amber",
                        "wardrobe": {"C1": "dark coat"},
                    }
                ],
                "segments": [
                    {
                        "index": 0,
                        "start_sec": 0,
                        "end_sec": 4,
                        "duration_sec": 4,
                        "scene_ids": ["S1"],
                        "cast_present": ["C1"],
                        "assembly_mode": "single_take_i2v",
                        "sub_shots": [
                            {
                                "global_index": 0,
                                "start_sec": 0,
                                "end_sec": 4,
                                "duration_sec": 4,
                                "scene_id": "S1",
                                "cast_present": ["C1"],
                            }
                        ],
                        "shot_windows": [{"t": [0, 4], "sub_global_index": 0}],
                    }
                ],
            }
        )
    )
    audio.write_bytes(b"fake")
    config = tmp_path / "run-config.json"
    config.write_text(
        json.dumps(
            {
                "video": {"provider": "seedance"},
                "providers": {"seedance": {"official_identity_assets": {"C1": "asset://asset-preset-one"}}},
            }
        )
    )
    pipeline = runner.Pipeline(board, audio, tmp_path / "execution", config)
    pipeline.cut_audio = lambda segment: audio
    return pipeline


@pytest.mark.parametrize("asset_uri", ["asset://asset-preset-one", "asset://asset-user-uploaded-image"])
def test_seedance_base_assets_skip_generation_and_registration(official_seedance_pipeline, monkeypatch, asset_uri):
    pipeline = official_seedance_pipeline
    pipeline.video_provider.config["official_identity_assets"]["C1"] = asset_uri

    def unexpected(*args, **kwargs):
        pytest.fail("Existing asset binding must not call an API or generate an image")

    monkeypatch.setattr(pipeline, "request", unexpected)
    monkeypatch.setattr(pipeline, "generate_image", unexpected)
    pipeline.generate_base_assets()
    assert pipeline.state["assets"]["characters"] == {}
    identity = pipeline.state["assets"]["provider_identities"]["C1"]
    assert identity["asset_uri"] == asset_uri
    assert identity["source"] == "ark_image_asset"
    plan = pipeline.plan()
    assert plan["image_provider"] is None
    assert plan["models"]["image"] is None
    assert plan["identity_source"] == "ark_image_asset"
    refs = pipeline.segment_identity_assets(pipeline.segment_map[0])
    assert refs[0]["transport"] == "asset_uri"
    assert pipeline.identity_reference_url(refs[0]) == asset_uri
    assert refs[0]["source"] == "ark_image_asset"
    pipeline.prepare_video_packages()
    packages = json.loads((pipeline.output_dir / "reports/video_generation_packages.json").read_text())
    assert asset_uri in json.dumps(packages)
    assert "music2mv.invalid/identity" not in json.dumps(packages)


def test_seedance_missing_preset_blocks_old_generated_and_registered_fallbacks(official_seedance_pipeline):
    pipeline = official_seedance_pipeline
    pipeline.video_provider.config["official_identity_assets"] = {}
    pipeline.state["assets"]["characters"]["C1"] = {"status": "succeeded", "url": "https://example/old.jpg"}
    pipeline.state["assets"]["provider_identities"]["C1"] = {"status": "Active", "asset_uri": "asset://asset-private"}
    for operation in (
        pipeline.plan,
        pipeline.generate_base_assets,
        lambda: pipeline.prepare_segment(pipeline.segment_map[0]),
    ):
        with pytest.raises((ValueError, RuntimeError), match="Ark image asset"):
            operation()


def test_seedance_preset_change_invalidates_existing_task(official_seedance_pipeline):
    pipeline = official_seedance_pipeline
    first = pipeline.prepare_segment(pipeline.segment_map[0])
    first.update({"status": "succeeded", "task_id": "old-task"})
    pipeline.video_provider.config["official_identity_assets"]["C1"] = "asset://asset-preset-two"
    second = pipeline.prepare_segment(pipeline.segment_map[0])
    assert second["execution_signature"] != first["execution_signature"]
    assert "task_id" not in second
    assert second["status"] == "prepared"
    assert second["identity_assets"][0]["asset_uri"] == "asset://asset-preset-two"


@pytest.mark.parametrize(
    "uri",
    [
        "https://example/portrait.jpg",
        "data:image/png;base64,abc",
        "/tmp/person.jpg",
        "asset://",
        "asset://asset-id/path",
        "asset://asset-id?token=x",
    ],
)
def test_seedance_rejects_non_preset_image_references(uri):
    provider = SeedanceProvider(
        {"base_url": "https://ark.cn-beijing.volces.com", "api_key_env": "KEY", "model": "seedance"}
    )
    with pytest.raises(ValueError, match="Ark image asset"):
        provider.build_payload(
            {"video_prompt": "one shot", "api_duration_sec": 4}, [uri], "data:audio/wav;base64,abc", {}
        )


@pytest.mark.parametrize("legacy", ["identity_reference_urls", "register_identity_assets"])
def test_seedance_legacy_configuration_requires_explicit_migration(legacy):
    provider = SeedanceProvider(
        {"base_url": "https://ark.cn-beijing.volces.com", "api_key_env": "KEY", "model": "seedance", legacy: {}}
    )
    with pytest.raises(ValueError, match="Legacy Seedance"):
        provider.validate_configuration()
