"""Offline contracts and local render tests for Omni ChatCut Video Translation."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import wave
from array import array
from pathlib import Path

import pytest

import qwen_mm_plugins_omni_chatcut as chatcut
from qwen_mm_plugins_omni_chatcut.video_translation.contracts import (
    _estimated_spoken_duration,
    validate_delivery,
    validate_plan,
)
from qwen_mm_plugins_omni_chatcut.video_translation.project import (
    PLAN_SCHEMA,
    REVIEW_SCHEMA,
    VAD_SCHEMA,
    analysis_mode_for_duration,
    inspect_project,
    prepare_project,
)
from qwen_mm_plugins_omni_chatcut.video_translation.rendering import (
    _build_diagnostics,
    _fit_voice,
    _mix,
    _prepare_raw_voice,
    render_project,
)
from qwen_mm_plugins_omni_chatcut.video_translation.tools.render_project import _format_user_summary

EXPECTED_TOOLS = {
    "prepare_video_translation_project",
    "check_dubbing_service",
    "separate_dubbing_audio",
    "detect_dubbing_speech",
    "synthesize_dubbing_speech",
    "validate_video_translation_plan",
    "render_video_translation",
    "validate_video_translation_delivery",
}

CAPABILITY_DIR = Path(__file__).resolve().parents[2] / "src/capabilities/omni-chatcut"

# Keep pure contract/client tests runnable without the local media toolchain.
requires_media_tools = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for local media tests",
)


def test_dubbing_service_launcher_is_bundled_with_the_skill():
    reference_dir = CAPABILITY_DIR / "skill/video-translation/references"
    assert (reference_dir / "launch_dubbing_server.py").is_file()
    guide = (reference_dir / "dubbing-service.md").read_text(encoding="utf-8")
    assert "src/capabilities/api" not in guide


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _plan(root: Path) -> Path:
    manifest = json.loads((root / "project.json").read_text())
    transcript = {
        "source_movie": manifest["source_movie"],
        "source_sha256": manifest["source_sha256"],
        "source_language": manifest["source_language"],
        "segments": [
            {
                "segment_id": "SEG_0001",
                "speaker": "SPK_001",
                "start_sec": 0.3,
                "end_sec": 1.2,
                "source_text": "你好",
            },
            {
                "segment_id": "SEG_0002",
                "speaker": "SPK_001",
                "start_sec": 1.7,
                "end_sec": 2.6,
                "source_text": "再见",
            },
        ],
    }
    vad = {
        "schema": VAD_SCHEMA,
        "source_movie": manifest["source_movie"],
        "source_sha256": manifest["source_sha256"],
        "duration_sec": manifest["duration_sec"],
        "audio_source": "work/source/vocals.wav",
        "parameters": {"threshold": 0.5},
        "segments": [
            {"start_sec": 0.25, "end_sec": 1.25},
            {"start_sec": 1.65, "end_sec": 2.65},
        ],
    }
    transcript_path = root / "analysis/transcript.json"
    vad_path = root / "analysis/vad.json"
    _write(transcript_path, transcript)
    _write(vad_path, vad)
    plan = {
        "schema": PLAN_SCHEMA,
        "source_movie": manifest["source_movie"],
        "source_sha256": manifest["source_sha256"],
        "source_language": manifest["source_language"],
        "target_language": manifest["target_language"],
        "transcript_sha256": hashlib.sha256(transcript_path.read_bytes()).hexdigest(),
        "vad_sha256": hashlib.sha256(vad_path.read_bytes()).hexdigest(),
        "segments": [
            {
                "segment_id": "DUB_0001",
                "source_segment_ids": ["SEG_0001"],
                "speaker": "SPK_001",
                "start_sec": 0.3,
                "end_sec": 1.2,
                "source_text": "你好",
                "translated_text": "Hello",
                "reference": {
                    "source_segment_ids": ["SEG_0001"],
                    "start_sec": 0.3,
                    "end_sec": 1.2,
                    "selection_reason": "corresponding clean source delivery",
                },
            },
            {
                "segment_id": "DUB_0002",
                "source_segment_ids": ["SEG_0002"],
                "speaker": "SPK_001",
                "start_sec": 1.7,
                "end_sec": 2.6,
                "source_text": "再见",
                "translated_text": "Goodbye",
                "reference": {
                    "source_segment_ids": ["SEG_0002"],
                    "start_sec": 1.7,
                    "end_sec": 2.6,
                    "selection_reason": "corresponding clean source delivery",
                },
            },
        ],
    }
    path = root / "plan/translation_plan.json"
    _write(path, plan)
    return path


def test_video_translation_tools_are_discoverable():
    assert EXPECTED_TOOLS <= {item["name"] for item in chatcut.list_tools()}


def test_separate_audio_tool_writes_reusable_signature(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.video_translation.tools import separate_audio as tool

    source = tmp_path / "source.wav"
    source.write_bytes(b"source audio")
    output_dir = tmp_path / "stems"
    monkeypatch.setattr(
        tool,
        "separate_audio",
        lambda *args, **kwargs: {
            "stems": {
                "vocals": str(output_dir / "vocals.wav"),
                "no_vocals": str(output_dir / "no_vocals.wav"),
            }
        },
    )

    tool.handle({"input_path": str(source), "output_dir": str(output_dir), "model": "htdemucs"})

    signature = json.loads((output_dir / "separation.signature.json").read_text(encoding="utf-8"))
    assert signature == {"source_audio_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "model": "htdemucs"}


def test_source_analysis_mode_uses_ten_minute_boundary():
    assert analysis_mode_for_duration(600.0) == "single_full_video"
    assert analysis_mode_for_duration(600.001) == "bounded_windows"


@requires_media_tools
def test_prepare_and_validate_translation_plan(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    state = prepare_project(
        source_movie=sample_media_av,
        project_dir=str(root),
        source_language="zh",
        target_language="en",
    )
    assert state["recommended_stage"] == "analyze_source"
    assert state["analysis_mode"] == "single_full_video"
    project = json.loads((root / "project.json").read_text())
    assert project["duration_sec"] == state["duration_sec"]
    assert "analysis_mode" not in project
    assert not (root / "analysis/source_facts.json").exists()
    plan_path = _plan(root)
    assert validate_plan(str(root))["valid"] is True
    broken = json.loads(plan_path.read_text())
    broken["segments"][0]["end_sec"] = 99
    _write(plan_path, broken)
    assert validate_plan(str(root))["valid"] is False


@requires_media_tools
def test_plan_requires_persisted_vad_evidence(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), source_language="zh")
    _plan(root)
    (root / "analysis/vad.json").unlink()

    result = validate_plan(str(root))
    assert result["valid"] is False
    assert any("VAD evidence is missing" in error for error in result["errors"])


@requires_media_tools
def test_plan_treats_low_vad_overlap_as_agent_review_diagnostic(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), source_language="zh")
    plan_path = _plan(root)
    transcript_path = root / "analysis/transcript.json"
    transcript = json.loads(transcript_path.read_text())
    transcript["segments"][0]["start_sec"] = 0.0
    transcript["segments"][0]["end_sec"] = 0.2
    _write(transcript_path, transcript)
    plan = json.loads(plan_path.read_text())
    plan["transcript_sha256"] = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
    plan["segments"][0]["start_sec"] = 0.0
    plan["segments"][0]["end_sec"] = 0.2
    plan["segments"][0]["reference"]["start_sec"] = 0.0
    plan["segments"][0]["reference"]["end_sec"] = 0.2
    _write(plan_path, plan)

    result = validate_plan(str(root))

    assert result["valid"] is True
    assert result["evidence_diagnostics"][0] == {
        "segment_id": "SEG_0001",
        "vad_overlap_ratio": 0.0,
        "review_recommended": True,
    }


@requires_media_tools
def test_plan_allows_agent_to_merge_adjacent_same_speaker_segments(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), source_language="zh")
    plan_path = _plan(root)
    plan = json.loads(plan_path.read_text())
    plan["segments"] = [
        {
            "segment_id": "DUB_0001",
            "source_segment_ids": ["SEG_0001", "SEG_0002"],
            "merge_reason": "one delivery reads more naturally than two short fragments",
            "speaker": "SPK_001",
            "start_sec": 0.3,
            "end_sec": 2.6,
            "source_text": "你好，再见",
            "translated_text": "Hello, and goodbye.",
            "reference": {
                "source_segment_ids": ["SEG_0001", "SEG_0002"],
                "start_sec": 0.3,
                "end_sec": 2.6,
                "selection_reason": "corresponding clean source delivery extended for stability",
            },
        }
    ]
    _write(plan_path, plan)

    assert validate_plan(str(root))["valid"] is True


@requires_media_tools
def test_inspector_supports_legacy_source_facts_duration(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root))
    project_path = root / "project.json"
    project = json.loads(project_path.read_text())
    duration = project.pop("duration_sec")
    project["artifacts"]["source_facts"] = "analysis/source_facts.json"
    _write(project_path, project)
    _write(root / "analysis/source_facts.json", {"duration_sec": duration})

    state = inspect_project(root)
    assert state["duration_sec"] == duration
    assert state["analysis_mode"] == "single_full_video"


@requires_media_tools
def test_resume_rejects_changed_source_content(sample_media_av, tmp_path):
    source = tmp_path / "source.mp4"
    shutil.copyfile(sample_media_av, source)
    root = tmp_path / "translation"
    prepare_project(source_movie=str(source), project_dir=str(root))
    with source.open("ab") as handle:
        handle.write(b"changed")

    with pytest.raises(ValueError, match="source movie content changed"):
        prepare_project(source_movie=str(source), project_dir=str(root), resume=True)


@requires_media_tools
def test_plan_reports_large_duration_mismatch_as_agent_diagnostic(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(
        source_movie=sample_media_av,
        project_dir=str(root),
        source_language="zh",
        target_language="en",
    )
    plan_path = _plan(root)
    plan = json.loads(plan_path.read_text())
    plan["segments"][0]["translated_text"] = (
        "Hello there, I have an extremely long explanation that cannot possibly match this short line"
    )
    _write(plan_path, plan)
    result = validate_plan(str(root))
    assert result["valid"] is True
    assert result["timing_diagnostics"][0]["estimated_nominal_speedup"] > 1.18


@requires_media_tools
def test_plan_rejects_overlapping_segments(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), source_language="zh")
    plan_path = _plan(root)
    plan = json.loads(plan_path.read_text())
    plan["segments"][1]["start_sec"] = 1.0
    _write(plan_path, plan)
    transcript_path = root / "analysis/transcript.json"
    transcript = json.loads(transcript_path.read_text())
    transcript["segments"][1]["start_sec"] = 1.0
    _write(transcript_path, transcript)

    result = validate_plan(str(root))
    assert result["valid"] is False
    assert any("overlaps the previous segment" in error for error in result["errors"])


@requires_media_tools
def test_plan_rejects_cross_speaker_grouping_and_reference(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), source_language="zh")
    plan_path = _plan(root)
    plan = json.loads(plan_path.read_text())
    transcript_path = root / "analysis/transcript.json"
    transcript = json.loads(transcript_path.read_text())
    plan["segments"][1]["speaker"] = "SPK_002"
    transcript["segments"][1]["speaker"] = "SPK_002"
    plan["segments"][0]["reference"] = {
        "source_segment_ids": ["SEG_0002"],
        "start_sec": 1.7,
        "end_sec": 2.6,
        "selection_reason": "current segment is too short",
    }
    plan["transcript_sha256"] = hashlib.sha256(
        json.dumps(transcript, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    ).hexdigest()
    _write(plan_path, plan)
    _write(transcript_path, transcript)

    result = validate_plan(str(root))
    assert result["valid"] is False
    assert any("reference overlaps speech from another speaker" in error for error in result["errors"])


@requires_media_tools
def test_plan_allows_explained_same_speaker_reference_fallback(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), source_language="zh")
    plan_path = _plan(root)
    plan = json.loads(plan_path.read_text())
    plan["segments"][0]["reference"] = {
        "source_segment_ids": ["SEG_0002"],
        "start_sec": 1.7,
        "end_sec": 2.6,
        "selection_reason": "current segment contains severe separation artifacts",
    }
    _write(plan_path, plan)

    assert validate_plan(str(root))["valid"] is True


def test_japanese_duration_estimation_counts_kana():
    assert _estimated_spoken_duration("これはテストです", "ja") > _estimated_spoken_duration("日", "ja")


@requires_media_tools
def test_fit_voice_preserves_short_audio_pace_and_centers_remaining_silence(tmp_path):
    source = tmp_path / "short.wav"
    output = tmp_path / "fitted.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.35",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
    )
    report = _fit_voice(source, output, 0.9)
    assert report["atempo"] == 1.0
    assert report["stretch_ratio"] == 1.0
    assert 0.27 <= report["leading_silence_sec"] <= 0.28
    assert 0.27 <= report["trailing_silence_sec"] <= 0.28
    assert 0.38 <= report["speech_fill_ratio"] <= 0.4
    assert report["fade_in_sec"] == 0.005
    assert report["fade_out_sec"] == 0.02
    assert report["sample_rate"] == 48000
    assert report["channels"] == 2
    assert "loudness" not in report
    stream_probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels",
                "-of",
                "json",
                str(output),
            ],
            text=True,
        )
    )["streams"][0]
    assert stream_probe == {"sample_rate": "48000", "channels": 2}
    assert (
        0.85
        <= float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(output),
                ],
                text=True,
            ).strip()
        )
        <= 0.95
    )

    with wave.open(str(output), "rb") as audio:
        samples = array("h", audio.readframes(audio.getnframes()))
        channels = audio.getnchannels()
        rate = audio.getframerate()
    left = samples[::channels]
    threshold = max(abs(sample) for sample in left) * 0.01
    audible = [index for index, sample in enumerate(left) if abs(sample) >= threshold]
    assert 0.25 <= audible[0] / rate <= 0.30
    assert 0.60 <= audible[-1] / rate <= 0.65


@requires_media_tools
def test_fit_voice_smooths_the_transition_to_padded_silence(tmp_path):
    source = tmp_path / "hard_edge.wav"
    output = tmp_path / "fitted.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=0.35025",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
    )

    _fit_voice(source, output, 0.9)

    with wave.open(str(output), "rb") as audio:
        samples = array("h", audio.readframes(audio.getnframes()))
        channels = audio.getnchannels()
    left = samples[::channels]
    peak = max(abs(sample) for sample in left)
    largest_step = max(abs(current - previous) for previous, current in zip(left, left[1:]))
    assert largest_step < peak * 0.25


@requires_media_tools
def test_fit_voice_rejects_speedup_above_limit(tmp_path):
    source = tmp_path / "long.wav"
    output = tmp_path / "fitted.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1.25",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="too long for its slot"):
        _fit_voice(source, output, 1.0)
    assert not output.exists()


@requires_media_tools
def test_overlong_tts_retries_only_the_current_segment(monkeypatch, tmp_path):
    durations = iter((1.25, 0.95))
    calls = []

    def fake_tts(*, output_path: str, **kwargs):
        duration = next(durations)
        calls.append(duration)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=880:duration={duration}",
                "-c:a",
                "pcm_s16le",
                output_path,
            ],
            check=True,
        )

    monkeypatch.setattr("qwen_mm_plugins_omni_chatcut.video_translation.rendering.synthesize_speech", fake_tts)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    raw = tmp_path / "raw.wav"
    signature_path = tmp_path / "raw.signature.json"
    result = _prepare_raw_voice(
        text="Hello",
        target_language="en",
        reference_path=reference,
        raw=raw,
        signature_path=signature_path,
        signature="signature",
        signature_value={"translated_text": "Hello", "reference_sha256": "reference"},
        target_duration=1.0,
        explicit_server=None,
        reuse_existing=True,
    )

    assert calls == [1.25, 0.95]
    assert result["tts_generated_attempts"] == 2
    assert result["tts_candidate_durations_sec"] == [1.25, 0.95]
    assert result["raw_tts_reused"] is False
    assert (
        0.9
        <= float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(raw),
                ],
                text=True,
            ).strip()
        )
        <= 1.0
    )


@requires_media_tools
def test_clearly_long_translation_does_not_keep_retrying(monkeypatch, tmp_path):
    calls = 0

    def fake_tts(*, output_path: str, **kwargs):
        nonlocal calls
        calls += 1
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:duration=1.3",
                "-c:a",
                "pcm_s16le",
                output_path,
            ],
            check=True,
        )

    monkeypatch.setattr("qwen_mm_plugins_omni_chatcut.video_translation.rendering.synthesize_speech", fake_tts)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    with pytest.raises(ValueError, match="above the 1.35x retry threshold"):
        _prepare_raw_voice(
            text="This translated sentence is intentionally much too long for the available speaking slot.",
            target_language="en",
            reference_path=reference,
            raw=tmp_path / "raw.wav",
            signature_path=tmp_path / "raw.signature.json",
            signature="signature",
            signature_value={"translated_text": "long", "reference_sha256": "reference"},
            target_duration=1.0,
            explicit_server=None,
            reuse_existing=True,
        )
    assert calls == 1


@requires_media_tools
def test_mix_keeps_voice_audible_without_automatic_ducking(tmp_path):
    background = tmp_path / "background.wav"
    voice = tmp_path / "voice.wav"
    mixed = tmp_path / "mixed.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=2",
            "-c:a",
            "pcm_s16le",
            str(background),
        ],
        check=True,
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
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "2",
            str(voice),
        ],
        check=True,
    )
    _mix(background, [(voice, 0.25)], mixed, 2.0)
    stats = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(mixed), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    mean_match = re.search(r"mean_volume: (-?[0-9.]+) dB", stats)
    assert mean_match is not None
    assert float(mean_match.group(1)) > -30.0


@requires_media_tools
def test_mix_can_omit_background(tmp_path):
    voice = tmp_path / "voice.wav"
    mixed = tmp_path / "mixed.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=0.5",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "2",
            str(voice),
        ],
        check=True,
    )

    _mix(None, [(voice, 0.25)], mixed, 1.0)

    assert mixed.is_file()
    assert (
        0.95
        <= float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(mixed),
                ],
                text=True,
            ).strip()
        )
        <= 1.05
    )


@requires_media_tools
def test_mix_preserves_late_voice_timeline_position(tmp_path):
    first = tmp_path / "first.wav"
    last = tmp_path / "last.wav"
    mixed = tmp_path / "mixed.wav"
    for path, frequency in ((first, 440), (last, 1200)):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration=0.5",
                "-c:a",
                "pcm_s16le",
                "-ac",
                "2",
                str(path),
            ],
            check=True,
        )

    _mix(None, [(first, 1.0), (last, 4.0)], mixed, 6.0)

    with wave.open(str(mixed), "rb") as audio:
        samples = array("h", audio.readframes(audio.getnframes()))
        channels = audio.getnchannels()
        rate = audio.getframerate()
    left = samples[::channels]

    def rms(start: float, end: float) -> float:
        window = left[round(start * rate) : round(end * rate)]
        return (sum(sample * sample for sample in window) / len(window)) ** 0.5

    assert rms(0.0, 0.5) < 10
    assert rms(1.05, 1.45) > 100
    assert rms(3.0, 3.5) < 10
    assert rms(4.05, 4.45) > 100


@requires_media_tools
def test_mix_does_not_wrap_final_voice_to_the_start(tmp_path):
    background = tmp_path / "background.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=6.0",
            "-c:a",
            "pcm_s16le",
            str(background),
        ],
        check=True,
    )
    voices = []
    for index in range(19):
        voice = tmp_path / f"voice-{index:02d}.wav"
        frequency = 1200 if index == 18 else 440 + index * 10
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration=0.2",
                "-c:a",
                "pcm_s16le",
                "-ac",
                "2",
                str(voice),
            ],
            check=True,
        )
        voices.append((voice, 0.25 + 0.25 * index))
    voices[-1] = (voices[-1][0], 5.79)
    mixed = tmp_path / "mixed.wav"

    _mix(background, voices, mixed, 6.0)

    with wave.open(str(mixed), "rb") as audio:
        samples = array("h", audio.readframes(audio.getnframes()))
        channels = audio.getnchannels()
        rate = audio.getframerate()
    left = samples[::channels]

    def rms(start: float, end: float) -> float:
        window = left[round(start * rate) : round(end * rate)]
        return (sum(sample * sample for sample in window) / len(window)) ** 0.5

    assert rms(0.0, 0.15) < 10
    assert rms(5.82, 5.95) > 100


def test_diagnostics_puts_review_segments_first_and_returns_reasons(tmp_path):
    segment_reports = [
        {
            "segment_id": "DUB_0001",
            "slot_start_sec": 0.0,
            "slot_end_sec": 1.0,
            "source_text": "你好",
            "translated_text": "Hello",
            "atempo": 1.0,
            "stretch_ratio": 1.0,
            "risk_flags": [],
            "risk_messages": [],
            "reference_audio": "/tmp/ref-1.wav",
        },
        {
            "segment_id": "DUB_0002",
            "slot_start_sec": 1.0,
            "slot_end_sec": 2.0,
            "source_text": "再见",
            "translated_text": "Goodbye",
            "atempo": 1.15,
            "stretch_ratio": 1.0,
            "risk_flags": ["acceleration_above_1.12x"],
            "risk_messages": ["speech was accelerated above 1.12x; listen for rushed delivery"],
            "reference_audio": "/tmp/ref-2.wav",
        },
    ]
    diagnostics = _build_diagnostics(
        tmp_path,
        segment_reports,
        {"valid": True, "errors": [], "slots": []},
        2.0,
    )

    assert diagnostics["summary"] == {"pass_count": 1, "review_count": 1, "risk_count": 1}
    assert diagnostics["review_segments"][0]["segment_id"] == "DUB_0002"
    summary = (tmp_path / "full/translation_summary.md").read_text()
    assert "## Needs manual review" in summary
    assert "DUB_0002" in summary
    assert "rushed delivery" in summary
    assert summary.index("Needs manual review") < summary.index("All segments")


def test_render_tool_returns_concise_human_summary():
    result = {
        "segment_count": 2,
        "summary": {"pass_count": 1, "review_count": 1},
        "review_segments": [
            {
                "segment_id": "DUB_0002",
                "slot_start_sec": 1.0,
                "slot_end_sec": 2.0,
                "reasons": ["speech was accelerated above 1.12x; listen for rushed delivery"],
            }
        ],
        "summary_path": "/tmp/summary.md",
        "diagnostics_path": "/tmp/diagnostics.json",
        "final_video": "/tmp/translated.mp4",
    }
    rendered = _format_user_summary(result)
    assert rendered.startswith("翻译渲染完成")
    assert "共 2 段：1 段正常，1 段建议复核。" in rendered
    assert "DUB_0002（1.00–2.00s）" in rendered
    assert rendered.count("{") == 0


@requires_media_tools
def test_inspector_routes_transcript_and_plan(sample_media_av, tmp_path):
    root = tmp_path / "translation"
    prepare_project(source_movie=sample_media_av, project_dir=str(root))
    _write(root / "analysis/transcript.json", {"segments": []})
    assert inspect_project(root)["recommended_stage"] == "analyze_source"
    _plan(root)
    assert inspect_project(root)["recommended_stage"] == "render_or_resume"


def test_external_client_decodes_audio_without_returning_base64(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.video_translation import client

    class Response:
        def json(self):
            payload = base64.b64encode(b"RIFF-test").decode()
            return {"audio_base64": payload, "duration_sec": 1.0, "sample_rate": 24000}

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: Response())
    output = tmp_path / "voice.wav"
    result = client.synthesize_speech(
        text="hello", reference_audio=str(reference), output_path=str(output), explicit_server="http://unused"
    )
    assert output.read_bytes() == b"RIFF-test"
    assert "audio_base64" not in result


def test_external_client_uses_default_emotion_settings(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.video_translation import client

    captured = {}

    class Response:
        def json(self):
            return {"audio_base64": base64.b64encode(b"RIFF-test").decode()}

    def fake_request(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    monkeypatch.setattr(client, "_request", fake_request)
    client.synthesize_speech(
        text="hello",
        reference_audio=str(reference),
        output_path=str(tmp_path / "voice.wav"),
        explicit_server="http://unused",
    )
    assert "emo_alpha" not in captured
    assert "use_emo_text" not in captured
    assert "emo_text" not in captured


def test_external_client_rejects_invalid_vad_intervals(monkeypatch, tmp_path):
    from qwen_mm_plugins_omni_chatcut.video_translation import client

    class Response:
        def json(self):
            return {
                "duration": 2.0,
                "sample_rate": 16000,
                "segments": [
                    {"start_time": 1.0, "end_time": 1.5},
                    {"start_time": 1.4, "end_time": 1.8},
                ],
            }

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="invalid VAD interval"):
        client.detect_speech(str(audio), explicit_server="http://unused")


def test_external_client_falls_back_to_curl_on_bad_file_descriptor(monkeypatch):
    from qwen_mm_plugins_omni_chatcut.video_translation import client

    class BrokenRequests:
        @staticmethod
        def request(*args, **kwargs):
            raise OSError(9, "Bad file descriptor")

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        body_arg = command[command.index("--data-binary") + 1]
        captured["payload"] = json.loads(Path(body_arg.removeprefix("@")).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b'{"status":"ok","model_loaded":true}',
            stderr=b"",
        )

    monkeypatch.setattr(client, "_requests", lambda: BrokenRequests())
    monkeypatch.setattr(client, "which_tool", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(client.subprocess, "run", fake_run)

    response = client._request(
        "POST",
        "http://example.invalid/health",
        timeout=10,
        json={"message": "测试"},
    )
    assert response.json() == {"status": "ok", "model_loaded": True}
    assert captured["payload"] == {"message": "测试"}
    assert captured["command"][-1] == "http://example.invalid/health"


@requires_media_tools
def test_local_renderer_and_delivery_validation(sample_media_av, tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    shutil.copyfile(sample_media_av, source)
    root = tmp_path / "translation"
    prepare_project(
        source_movie=str(source),
        project_dir=str(root),
        source_language="zh",
        target_language="en",
    )
    _plan(root)

    def fake_separate(input_path: str, output_dir: str, **kwargs):
        stems = {}
        for name in ("vocals", "no_vocals"):
            output = Path(output_dir) / f"{name}.wav"
            output.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    input_path,
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(output),
                ],
                check=True,
            )
            stems[name] = str(output)
        return {"stems": stems}

    monkeypatch.setattr("qwen_mm_plugins_omni_chatcut.video_translation.rendering.separate_audio", fake_separate)

    stale_source = root / "work/source/source.wav"
    source_dir = stale_source.parent
    source_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(stale_source),
        ],
        check=True,
    )
    for name in ("vocals.wav", "no_vocals.wav"):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(source_dir / name),
            ],
            check=True,
        )

    def fake_tts(*, output_path: str, **kwargs):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:duration=0.5",
                "-c:a",
                "pcm_s16le",
                output_path,
            ],
            check=True,
        )
        return {"output_path": output_path, "duration_sec": 0.5}

    monkeypatch.setattr("qwen_mm_plugins_omni_chatcut.video_translation.rendering.synthesize_speech", fake_tts)
    result = render_project(str(root), reuse_existing=True)
    assert inspect_project(root)["recommended_stage"] == "review_delivery"
    missing_review = validate_delivery(str(root))
    assert missing_review["valid"] is False
    assert "full/agent_review.json is missing" in missing_review["errors"]
    _write(
        root / "full/agent_review.json",
        {
            "schema": REVIEW_SCHEMA,
            "output_sha256": hashlib.sha256(Path(result["final_video"]).read_bytes()).hexdigest(),
            "plan_sha256": hashlib.sha256((root / "plan/translation_plan.json").read_bytes()).hexdigest(),
            "reviewed_segment_ids": ["DUB_0001", "DUB_0002"],
            "checks": {
                "translation": True,
                "timing": True,
                "voice_reference": True,
                "natural_delivery": True,
                "mix": True,
            },
            "issues": [],
            "overall_pass": True,
        },
    )
    assert inspect_project(root)["recommended_stage"] == "validate_delivery"
    source_probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "json",
            str(stale_source),
        ],
        text=True,
    )
    source_stream = json.loads(source_probe)["streams"][0]
    assert source_stream["sample_rate"] == "48000"
    assert source_stream["channels"] == 2
    mixed_probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "json",
            str(root / "work/audio/mixed.wav"),
        ],
        text=True,
    )
    mixed_stream = json.loads(mixed_probe)["streams"][0]
    assert mixed_stream == {"sample_rate": "48000", "channels": 2}
    background_probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "json",
            str(root / "work/audio/background_48k.wav"),
        ],
        text=True,
    )
    background_stream = json.loads(background_probe)["streams"][0]
    assert background_stream == {"sample_rate": "48000", "channels": 2}
    final_probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "json",
            result["final_video"],
        ],
        text=True,
    )
    final_stream = json.loads(final_probe)["streams"][0]
    assert final_stream == {"sample_rate": "48000", "channels": 2}
    assert Path(result["final_video"]).is_file()
    assert validate_delivery(str(root))["valid"] is True

    plan_path = root / "plan/translation_plan.json"
    original_plan = plan_path.read_text(encoding="utf-8")
    changed_plan = json.loads(original_plan)
    changed_plan["segments"][0]["translated_text"] = "Hello!"
    _write(plan_path, changed_plan)
    changed_plan_result = validate_delivery(str(root))
    assert changed_plan_result["valid"] is False
    assert "translation plan changed after rendering" in changed_plan_result["errors"]
    plan_path.write_text(original_plan, encoding="utf-8")

    with source.open("ab") as handle:
        handle.write(b"changed")
    changed_source = validate_delivery(str(root))
    assert changed_source["valid"] is False
    assert "source movie content changed after rendering" in changed_source["errors"]
    shutil.copyfile(sample_media_av, source)

    Path(result["final_video"]).write_bytes(b"not a video")
    assert validate_delivery(str(root))["valid"] is False
