"""Offline contract tests for Omni ChatCut Movie Commentary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

import qwen_mm_plugins_omni_chatcut as chatcut
from qwen_mm_plugins_omni_chatcut.movie_commentary.contracts import shard_plan, validate_delivery, validate_plan
from qwen_mm_plugins_omni_chatcut.movie_commentary.project import (
    PLAN_SCHEMA,
    QA_SCHEMA,
    REPORT_SCHEMA,
    inspect_project,
    prepare_project,
)

CAP_DIR = Path(REPO_ROOT) / "src/capabilities/omni-chatcut"
SKILL_DIR = CAP_DIR / "skill/movie-commentary"
MOVIE_TOOLS = {
    "prepare_movie_commentary_project",
    "validate_movie_commentary_plan",
    "shard_movie_commentary_plan",
    "validate_movie_commentary_delivery",
}


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _complete_facts(root: Path) -> None:
    path = root / "plan/execution_facts.json"
    facts = json.loads(path.read_text())
    facts.update(
        {
            "credits_start_sec": 2.9,
            "source_cut_max_sec": 2.8,
            "burned_subtitle_band": {"present": False},
            "output_canvas": {"width": 160, "height": 120, "aspect_policy": "preserve"},
            "subtitle_style": {"font": "DejaVu Sans", "alignment": "bottom"},
        }
    )
    _write(path, facts)


def _valid_plan(root: Path, source: str, count: int = 3) -> Path:
    notes = root / "plan/watch_notes/chunk_000.md"
    notes.write_text("absolute interval 0.0-3.0; observed test pattern.\n")
    segments = []
    script = ["# Narration", ""]
    for index in range(1, count + 1):
        segment_id = f"SEG_{index:04d}"
        narration = f"Evidence grounded narration {index}."
        segments.append(
            {
                "segment_id": segment_id,
                "narration": {"text": narration, "estimated_duration_sec": 1.0},
                "visual_plan": {
                    "target": "test pattern",
                    "rough_interval_sec": [0.0, 2.5],
                    "movie_locator": {
                        "scene_anchor": "test pattern",
                        "visible_cues": ["bars"],
                        "audio_cues": ["tone"],
                        "evidence_refs": ["plan/watch_notes/chunk_000.md"],
                    },
                },
                "audio_plan": {"source_audio_mode": "ducked_bed", "bgm_mode": "none"},
            }
        )
        script.extend((f"## {segment_id}", narration, ""))
    plan = {"schema": PLAN_SCHEMA, "source_movie": source, "language": "en", "segments": segments}
    path = root / "plan/editing_plan.json"
    _write(path, plan)
    (root / "plan/narration_script.md").write_text("\n".join(script), encoding="utf-8")
    return path


def _report_segment(segment_id: str) -> dict:
    return {
        "segment_id": segment_id,
        "status": "resolved",
        "narration_text": "text",
        "understanding_mode": "omni_grounded",
        "evidence_refs": ["plan/watch_notes/chunk_000.md"],
        "external_errors": [],
        "source_cuts": [{"start_sec": 0.0, "end_sec": 1.0, "evidence": "chunk_000"}],
        "sentence_boundaries": [[0.0, 1.0]],
        "subtitle_cues": [],
        "voice": {"engine": "edge-tts", "name": "voice", "rate": "+20%", "attempts": 1},
        "source_audio_mode": "ducked_bed",
        "highlight_windows": [],
        "bgm": {"mode": "none"},
        "shard_start_sec": 0.0,
        "shard_end_sec": 1.0,
        "final_film_start_sec": None,
        "final_film_end_sec": None,
        "audio_measurements": {},
        "video_measurements": {},
        "content_discrepancy": None,
    }


def _report(root: Path, shard_path: Path) -> None:
    shard = json.loads(shard_path.read_text())
    shard_id = shard["shard_id"]
    out = root / "out" / shard_id
    out.mkdir(parents=True, exist_ok=True)
    video = out / f"{shard_id}.mp4"
    video.write_bytes(b"fake video")
    segments = [_report_segment(item["segment_id"]) for item in shard["segments"]]
    _write(
        out / "exec_report.json",
        {
            "schema": REPORT_SCHEMA,
            "shard_id": shard_id,
            "status": "success",
            "source_movie": shard["source_movie"],
            "execution_facts_path": shard["execution_facts_path"],
            "output_video": str(video),
            "segment_count": len(segments),
            "segments": segments,
            "stream_facts": {},
            "duration_sec": len(segments),
            "frame_count": 10,
            "expected_frame_count": 10,
            "integrated_loudness_lufs": -18.0,
            "true_peak_dbtp": -1.6,
            "freeze_events": [],
            "black_events": [],
            "silence_events": [],
            "unresolved": [],
            "qa_checks": {"decode": True, "duration": True},
        },
    )


def test_movie_commentary_structure_and_discovery():
    names = {item["name"] for item in chatcut.list_tools()}
    assert MOVIE_TOOLS <= names
    assert {path.parent.name for path in (SKILL_DIR / "workflows").glob("*/WORKFLOW.md")} == {
        "source-analysis",
        "commentary-authoring",
        "rendering",
    }
    assert not list((SKILL_DIR / "workflows").rglob("SKILL.md"))
    assert (CAP_DIR / "agents/movie-commentary-planner.md").is_file()
    assert (CAP_DIR / "agents/movie-commentary-executor.md").is_file()


def test_prepare_project_probes_source_and_refuses_nonempty(sample_media_av, tmp_path):
    root = tmp_path / "project"
    state = prepare_project(source_movie=sample_media_av, project_dir=str(root), target="full", language="en")
    assert state["recommended_stage"] == "analyze_source"
    manifest = json.loads((root / "project.json").read_text())
    facts = json.loads((root / "plan/execution_facts.json").read_text())
    assert manifest["source_movie"] == str(Path(sample_media_av).resolve())
    assert facts["duration_sec"] == pytest.approx(3.0, abs=0.1)
    assert facts["selected_video_stream"]["width"] == 160
    with pytest.raises(FileExistsError):
        prepare_project(source_movie=sample_media_av, project_dir=str(root))
    resumed = prepare_project(source_movie=sample_media_av, project_dir=str(root), target="resume", resume=True)
    assert resumed["manifest"].endswith("project.json")


def test_plan_validation_and_safe_evidence(sample_media_av, tmp_path):
    root = tmp_path / "project"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), language="en")
    _complete_facts(root)
    plan_path = _valid_plan(root, str(Path(sample_media_av).resolve()))
    assert validate_plan(str(root))["valid"] is True
    data = json.loads(plan_path.read_text())
    data["segments"][0]["visual_plan"]["movie_locator"]["evidence_refs"] = ["../secret.md"]
    _write(plan_path, data)
    result = validate_plan(str(root))
    assert result["valid"] is False
    assert any("unsafe evidence" in error for error in result["errors"])


def test_plan_sharding_is_immutable_and_preserves_segments(sample_media_av, tmp_path):
    root = tmp_path / "project"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), language="en")
    _complete_facts(root)
    _valid_plan(root, str(Path(sample_media_av).resolve()), count=3)
    result = shard_plan(str(root), segments_per_shard=2)
    assert result["shard_count"] == 2
    first = json.loads(Path(result["shards"][0]).read_text())
    assert [item["segment_id"] for item in first["segments"]] == ["SEG_0001", "SEG_0002"]
    with pytest.raises(FileExistsError):
        shard_plan(str(root), segments_per_shard=2)


def test_delivery_requires_uniform_reports_and_all_true_final_qa(sample_media_av, tmp_path):
    root = tmp_path / "project"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), language="en")
    _complete_facts(root)
    _valid_plan(root, str(Path(sample_media_av).resolve()), count=2)
    frozen = shard_plan(str(root), segments_per_shard=1)
    for path in map(Path, frozen["shards"]):
        _report(root, path)
    assert validate_delivery(str(root), require_final=False)["valid"] is True
    (root / "full/commentary.mp4").write_bytes(b"final")
    _write(
        root / "full/orchestrator_qa_full.json",
        {"schema": QA_SCHEMA, "checks": {"decode": True, "seams": True}, "overall_pass": True},
    )
    assert validate_delivery(str(root))["valid"] is True
    report = root / "out/shard_01/exec_report.json"
    broken = json.loads(report.read_text())
    broken.pop("true_peak_dbtp")
    _write(report, broken)
    failed = validate_delivery(str(root))
    assert failed["valid"] is False
    assert any("missing keys" in error for error in failed["errors"])


def test_state_inspector_routes_completed_project(sample_media_av, tmp_path):
    root = tmp_path / "project"
    prepare_project(source_movie=sample_media_av, project_dir=str(root), language="en")
    _complete_facts(root)
    (root / "plan/watch_notes/chunk.md").write_text("evidence")
    state = inspect_project(root)
    assert state["recommended_stage"] == "author_plan"
    script = SKILL_DIR / "scripts/inspect_movie_commentary_state.py"
    completed = subprocess.run([sys.executable, str(script), str(root)], capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout)["recommended_stage"] == "author_plan"
