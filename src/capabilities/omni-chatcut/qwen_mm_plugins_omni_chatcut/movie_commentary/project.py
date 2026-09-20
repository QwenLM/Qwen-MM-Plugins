"""Durable project initialization and state inspection for movie commentary."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from shared.video import probe_media

PROJECT_SCHEMA = "omni-chatcut/movie-commentary-project/v1"
PLAN_SCHEMA = "omni-chatcut/movie-commentary-plan/v1"
SHARD_SCHEMA = "omni-chatcut/movie-commentary-shard/v1"
REPORT_SCHEMA = "omni-chatcut/movie-commentary-exec-report/v1"
QA_SCHEMA = "omni-chatcut/movie-commentary-final-qa/v1"
TARGETS = {"analysis_only", "plan_only", "full", "resume"}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write UTF-8 JSON so interrupted runs do not leave half a contract."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def _fps(stream: dict[str, Any]) -> float | None:
    value = str(stream.get("r_frame_rate") or "")
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator) if float(denominator) else None
    except (ValueError, ZeroDivisionError):
        return None


def source_facts(source: Path) -> dict[str, Any]:
    probe = probe_media(str(source))
    streams = probe.get("streams") or []
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if not videos:
        raise ValueError(f"source has no video stream: {source}")
    if not audios:
        raise ValueError(f"source has no audio stream: {source}")
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    if duration <= 0:
        raise ValueError(f"source has no positive duration: {source}")
    video = videos[0]
    audio = audios[0]
    return {
        "path": str(source),
        "duration_sec": round(duration, 3),
        "format_name": (probe.get("format") or {}).get("format_name"),
        "size_bytes": source.stat().st_size,
        "selected_video_stream": {
            "index": video.get("index"),
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "sample_aspect_ratio": video.get("sample_aspect_ratio"),
            "display_aspect_ratio": video.get("display_aspect_ratio"),
            "fps": _fps(video),
        },
        "selected_audio_stream": {
            "index": audio.get("index"),
            "codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
            "channel_layout": audio.get("channel_layout"),
        },
        "subtitle_streams": [
            {"index": item.get("index"), "codec": item.get("codec_name")}
            for item in streams
            if item.get("codec_type") == "subtitle"
        ],
    }


def prepare_project(
    *,
    source_movie: str,
    project_dir: str,
    target: str = "full",
    language: str = "zh",
    style_brief: str = "",
    resume: bool = False,
) -> dict[str, Any]:
    source = Path(source_movie).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source_movie is not a readable file: {source}")
    root = Path(project_dir).expanduser().resolve()
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    manifest_path = root / "project.json"
    if root.exists() and any(root.iterdir()):
        if not resume:
            raise FileExistsError(f"project_dir is not empty; pass resume=true to continue: {root}")
        manifest = read_json(manifest_path)
        if manifest.get("schema") != PROJECT_SCHEMA:
            raise ValueError(f"not a movie-commentary project: {manifest_path}")
        if Path(manifest.get("source_movie", "")).resolve() != source:
            raise ValueError("resume source_movie differs from the project manifest")
        return inspect_project(root)

    for relative in (
        "plan/watch_notes",
        "plan/provenance",
        "shards",
        "out",
        "full",
        "work/analysis_clips",
        "work/orchestrator",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    facts = source_facts(source)
    facts.update(
        {
            "schema": "omni-chatcut/movie-commentary-execution-facts/v1",
            "credits_start_sec": None,
            "source_cut_max_sec": None,
            "burned_subtitle_band": None,
            "output_canvas": None,
            "subtitle_style": None,
            "picture_lead_sec": 0.3,
            "language": language,
            "audio_defaults": {
                "voice_engine": "edge-tts",
                "voice": "zh-CN-YunxiNeural" if language.startswith("zh") else None,
                "rate": "+20%",
                "atempo": None,
                "vo_target_lufs": -19,
                "program_target_lufs": -18,
                "true_peak_max_dbtp": -1.5,
                "source_audio_default_mode": "ducked_bed",
            },
            "bgm_manifest": None,
        }
    )
    execution_facts = root / "plan/execution_facts.json"
    write_json(execution_facts, facts)
    manifest = {
        "schema": PROJECT_SCHEMA,
        "target": target,
        "source_movie": str(source),
        "project_root": str(root),
        "language": language,
        "style_brief": style_brief,
        "artifacts": {
            "execution_facts": "plan/execution_facts.json",
            "watch_notes": "plan/watch_notes",
            "editing_plan": "plan/editing_plan.json",
            "narration_script": "plan/narration_script.md",
            "shards": "shards",
            "execution": "out",
            "final_video": "full/commentary.mp4",
            "final_qa": "full/orchestrator_qa_full.json",
        },
        "stages": {"analysis": "pending", "authoring": "pending", "execution": "pending"},
        "blockers": [],
    }
    write_json(manifest_path, manifest)
    return inspect_project(root)


def inspect_project(project_dir: str | Path) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    manifest_path = root / "project.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"project manifest is missing: {manifest_path}")
    manifest = read_json(manifest_path)
    artifacts = manifest.get("artifacts") or {}

    def exists(key: str) -> bool:
        value = artifacts.get(key)
        return bool(value and (root / value).exists())

    facts_complete = False
    if exists("execution_facts"):
        facts = read_json(root / artifacts["execution_facts"])
        facts_complete = all(
            facts.get(key) is not None
            for key in (
                "credits_start_sec",
                "source_cut_max_sec",
                "burned_subtitle_band",
                "output_canvas",
                "subtitle_style",
            )
        )
    notes_dir = root / str(artifacts.get("watch_notes") or "plan/watch_notes")
    note_count = len(list(notes_dir.glob("*.md"))) if notes_dir.is_dir() else 0
    reports = sorted(root.glob("out/shard_*/exec_report.json"))
    final_ready = exists("final_video") and exists("final_qa")
    if final_ready:
        recommended = "validate_delivery"
    elif exists("editing_plan") and list(root.glob("shards/shard_*.json")):
        recommended = "render_or_resume"
    elif note_count and facts_complete:
        recommended = "author_plan"
    else:
        recommended = "analyze_source"
    return {
        "project_root": str(root),
        "manifest": str(manifest_path),
        "schema": manifest.get("schema"),
        "source_movie": manifest.get("source_movie"),
        "target": manifest.get("target"),
        "execution_facts_complete": facts_complete,
        "watch_note_count": note_count,
        "plan_exists": exists("editing_plan"),
        "shard_count": len(list(root.glob("shards/shard_*.json"))),
        "report_count": len(reports),
        "final_video_exists": exists("final_video"),
        "final_qa_exists": exists("final_qa"),
        "recommended_stage": recommended,
    }
