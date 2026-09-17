"""Durable project state for Omni ChatCut video translation and dubbing."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from shared.video import probe_media

PROJECT_SCHEMA = "omni-chatcut/video-translation-project/v1"
VAD_SCHEMA = "omni-chatcut/video-translation-vad/v1"
PLAN_SCHEMA = "omni-chatcut/video-translation-plan/v2"
REPORT_SCHEMA = "omni-chatcut/video-translation-render-report/v2"
QA_SCHEMA = "omni-chatcut/video-translation-final-qa/v1"
REVIEW_SCHEMA = "omni-chatcut/video-translation-agent-review/v1"
TARGETS = {"analysis_only", "translation_only", "full", "resume"}
WHOLE_VIDEO_ANALYSIS_MAX_SEC = 10 * 60


def analysis_mode_for_duration(duration_sec: float) -> str:
    """Choose whether source analysis uses one request or bounded windows."""
    if duration_sec <= 0:
        raise ValueError("duration_sec must be positive")
    if duration_sec <= WHOLE_VIDEO_ANALYSIS_MAX_SEC:
        return "single_full_video"
    return "bounded_windows"


def write_json(path: Path, data: dict[str, Any]) -> None:
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
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_metadata(source: Path) -> dict[str, Any]:
    probe = probe_media(str(source))
    streams = probe.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    if video is None or audio is None or duration <= 0:
        raise ValueError("source must contain readable video and audio streams with positive duration")
    return {
        "source_sha256": file_sha256(source),
        "duration_sec": round(duration, 3),
    }


def project_duration(project_dir: str | Path, manifest: dict[str, Any] | None = None) -> float:
    """Read duration from project.json, with a fallback for legacy source_facts projects."""
    root = Path(project_dir).expanduser().resolve()
    project = manifest or read_json(root / "project.json")
    duration = float(project.get("duration_sec") or 0.0)
    if duration > 0:
        return duration

    artifacts = project.get("artifacts") or {}
    legacy_path = root / str(artifacts.get("source_facts") or "analysis/source_facts.json")
    legacy = read_json(legacy_path)
    duration = float(legacy.get("duration_sec") or 0.0)
    if duration <= 0:
        raise ValueError("project has no positive source duration")
    return duration


def prepare_project(
    *,
    source_movie: str,
    project_dir: str,
    source_language: str = "auto",
    target_language: str = "en",
    target: str = "full",
    style_brief: str = "",
    resume: bool = False,
) -> dict[str, Any]:
    source = Path(source_movie).expanduser().resolve()
    root = Path(project_dir).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"source_movie is not a readable file: {source}")
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    if not target_language.strip():
        raise ValueError("target_language is required")
    manifest_path = root / "project.json"
    if root.exists() and any(root.iterdir()):
        if not resume:
            raise FileExistsError(f"project_dir is not empty; pass resume=true to continue: {root}")
        manifest = read_json(manifest_path)
        if manifest.get("schema") != PROJECT_SCHEMA:
            raise ValueError(f"not a video-translation project: {manifest_path}")
        if Path(manifest.get("source_movie", "")).resolve() != source:
            raise ValueError("resume source_movie differs from project.json")
        if file_sha256(source) != manifest.get("source_sha256"):
            raise ValueError("source movie content changed after the project was created")
        return inspect_project(root)

    for relative in (
        "analysis",
        "plan",
        "work/source",
        "work/references",
        "work/tts_raw",
        "work/tts_fitted",
        "work/audio",
        "full",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    source_metadata = _source_metadata(source)
    manifest = {
        "schema": PROJECT_SCHEMA,
        "target": target,
        "source_movie": str(source),
        "source_sha256": source_metadata["source_sha256"],
        "duration_sec": source_metadata["duration_sec"],
        "project_root": str(root),
        "source_language": source_language,
        "target_language": target_language,
        "style_brief": style_brief,
        "artifacts": {
            "vad": "analysis/vad.json",
            "transcript": "analysis/transcript.json",
            "translation_plan": "plan/translation_plan.json",
            "render_report": "full/render_report.json",
            "translation_diagnostics": "full/translation_diagnostics.json",
            "translation_summary": "full/translation_summary.md",
            "final_video": "full/translated.mp4",
            "final_qa": "full/final_qa.json",
            "agent_review": "full/agent_review.json",
        },
        "stages": {"analysis": "pending", "translation": "pending", "rendering": "pending"},
        "blockers": [],
    }
    write_json(manifest_path, manifest)
    return inspect_project(root)


def inspect_project(project_dir: str | Path) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    manifest = read_json(root / "project.json")
    if manifest.get("schema") != PROJECT_SCHEMA:
        raise ValueError(f"project schema mismatch: {root / 'project.json'}")
    artifacts = manifest.get("artifacts") or {}
    duration = project_duration(root, manifest)

    def is_file(key: str) -> bool:
        value = artifacts.get(key)
        return bool(value and (root / value).is_file())

    vad_exists = is_file("vad")
    transcript_exists = is_file("transcript")
    if is_file("final_video") and is_file("final_qa") and is_file("agent_review"):
        recommended = "validate_delivery"
    elif is_file("final_video") and is_file("final_qa"):
        recommended = "review_delivery"
    elif vad_exists and transcript_exists and is_file("translation_plan"):
        recommended = "render_or_resume"
    elif vad_exists and transcript_exists:
        recommended = "author_translation"
    else:
        recommended = "analyze_source"
    return {
        "project_root": str(root),
        "manifest": str(root / "project.json"),
        "source_movie": manifest.get("source_movie"),
        "duration_sec": duration,
        "source_language": manifest.get("source_language"),
        "target_language": manifest.get("target_language"),
        "target": manifest.get("target"),
        "analysis_mode": analysis_mode_for_duration(duration),
        "vad_exists": vad_exists,
        "transcript_exists": transcript_exists,
        "plan_exists": is_file("translation_plan"),
        "render_report_exists": is_file("render_report"),
        "final_video_exists": is_file("final_video"),
        "final_qa_exists": is_file("final_qa"),
        "agent_review_exists": is_file("agent_review"),
        "recommended_stage": recommended,
    }
