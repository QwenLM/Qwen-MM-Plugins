"""Validation, sharding, and delivery contracts for movie commentary."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .project import PLAN_SCHEMA, QA_SCHEMA, REPORT_SCHEMA, SHARD_SCHEMA, read_json, write_json

REQUIRED_REPORT_KEYS = {
    "schema",
    "shard_id",
    "status",
    "source_movie",
    "execution_facts_path",
    "output_video",
    "segment_count",
    "segments",
    "stream_facts",
    "duration_sec",
    "frame_count",
    "expected_frame_count",
    "integrated_loudness_lufs",
    "true_peak_dbtp",
    "freeze_events",
    "black_events",
    "silence_events",
    "unresolved",
    "qa_checks",
}
REQUIRED_SEGMENT_REPORT_KEYS = {
    "segment_id",
    "status",
    "narration_text",
    "understanding_mode",
    "evidence_refs",
    "external_errors",
    "source_cuts",
    "sentence_boundaries",
    "subtitle_cues",
    "voice",
    "source_audio_mode",
    "highlight_windows",
    "bgm",
    "shard_start_sec",
    "shard_end_sec",
    "final_film_start_sec",
    "final_film_end_sec",
    "audio_measurements",
    "video_measurements",
    "content_discrepancy",
}


def _safe_evidence(root: Path, value: str) -> Path | None:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    resolved = (root / relative).resolve()
    notes = (root / "plan/watch_notes").resolve()
    try:
        resolved.relative_to(notes)
    except ValueError:
        return None
    return resolved


def validate_plan(project_dir: str, plan_path: str | None = None, require_evidence: bool = True) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    path = Path(plan_path).expanduser().resolve() if plan_path else root / "plan/editing_plan.json"
    data = read_json(path)
    errors: list[str] = []
    if data.get("schema") != PLAN_SCHEMA:
        errors.append(f"schema must be {PLAN_SCHEMA}")
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("segments must be a non-empty array")
        segments = []
    facts_path = root / "plan/execution_facts.json"
    manifest_path = root / "project.json"
    try:
        facts = read_json(facts_path)
    except (OSError, ValueError):
        facts = {}
        errors.append("plan/execution_facts.json is missing or invalid")
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError):
        manifest = {}
        errors.append("project.json is missing or invalid")
    if data.get("source_movie") != manifest.get("source_movie"):
        errors.append("plan source_movie differs from project.json")
    ceiling = facts.get("source_cut_max_sec")
    expected_ids: list[str] = []
    narration: list[tuple[str, str]] = []
    for index, segment in enumerate(segments, start=1):
        prefix = f"segments[{index - 1}]"
        expected = f"SEG_{index:04d}"
        expected_ids.append(expected)
        if not isinstance(segment, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if segment.get("segment_id") != expected:
            errors.append(f"{prefix}.segment_id must be {expected}")
        text = (segment.get("narration") or {}).get("text") if isinstance(segment.get("narration"), dict) else None
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{prefix}.narration.text is required")
        else:
            narration.append((expected, text.strip()))
        visual = segment.get("visual_plan") or {}
        rough = visual.get("rough_interval_sec") if isinstance(visual, dict) else None
        if not (isinstance(rough, list) and len(rough) == 2 and all(isinstance(item, (int, float)) for item in rough)):
            errors.append(f"{prefix}.visual_plan.rough_interval_sec must be [start,end]")
        elif rough[0] < 0 or rough[1] <= rough[0]:
            errors.append(f"{prefix}.visual_plan.rough_interval_sec is invalid")
        elif isinstance(ceiling, (int, float)) and rough[1] > ceiling:
            errors.append(f"{prefix}.visual_plan exceeds source_cut_max_sec")
        locator = visual.get("movie_locator") if isinstance(visual, dict) else None
        refs = locator.get("evidence_refs") if isinstance(locator, dict) else None
        if not isinstance(refs, list) or not refs:
            errors.append(f"{prefix}.visual_plan.movie_locator.evidence_refs is required")
        else:
            for ref in refs:
                evidence = _safe_evidence(root, str(ref))
                if evidence is None:
                    errors.append(f"{prefix} has unsafe evidence ref: {ref}")
                elif require_evidence and not evidence.is_file():
                    errors.append(f"{prefix} evidence does not exist: {ref}")
        audio = segment.get("audio_plan")
        if not isinstance(audio, dict) or audio.get("source_audio_mode") not in {"ducked_bed", "muted"}:
            errors.append(f"{prefix}.audio_plan.source_audio_mode must be ducked_bed or muted")
        if isinstance(audio, dict) and audio.get("bgm_mode", "none") not in {"none", "licensed"}:
            errors.append(f"{prefix}.audio_plan.bgm_mode must be none or licensed")
        if isinstance(audio, dict) and audio.get("bgm_mode") == "licensed" and not facts.get("bgm_manifest"):
            errors.append(f"{prefix} requests licensed BGM without bgm_manifest")
    script_path = root / "plan/narration_script.md"
    if script_path.is_file():
        script = script_path.read_text(encoding="utf-8")
        for segment_id, text in narration:
            if segment_id not in script or text not in script:
                errors.append(f"narration_script.md does not preserve {segment_id} verbatim")
    else:
        errors.append("plan/narration_script.md is missing")
    return {
        "valid": not errors,
        "plan_path": str(path),
        "segment_count": len(segments),
        "errors": errors,
    }


def shard_plan(
    project_dir: str,
    plan_path: str | None = None,
    segments_per_shard: int = 10,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not 1 <= segments_per_shard <= 20:
        raise ValueError("segments_per_shard must be between 1 and 20")
    validation = validate_plan(project_dir, plan_path, require_evidence=True)
    if not validation["valid"]:
        raise ValueError("plan validation failed: " + "; ".join(validation["errors"]))
    root = Path(project_dir).expanduser().resolve()
    data = read_json(Path(validation["plan_path"]))
    facts_path = root / "plan/execution_facts.json"
    outputs: list[str] = []
    segments = data["segments"]
    chunks = [segments[index : index + segments_per_shard] for index in range(0, len(segments), segments_per_shard)]
    for index, chunk in enumerate(chunks, start=1):
        path = root / f"shards/shard_{index:02d}.json"
        if path.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite shard: {path}")
        write_json(
            path,
            {
                "schema": SHARD_SCHEMA,
                "shard_id": f"shard_{index:02d}",
                "shard_index": index,
                "shard_count": len(chunks),
                "source_movie": data.get("source_movie"),
                "execution_facts_path": str(facts_path),
                "segment_count": len(chunk),
                "segments": chunk,
            },
        )
        outputs.append(str(path))
    return {"plan_path": validation["plan_path"], "shard_count": len(outputs), "shards": outputs}


def validate_delivery(project_dir: str, require_final: bool = True) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    errors: list[str] = []
    shards = sorted(root.glob("shards/shard_*.json"))
    if not shards:
        errors.append("no frozen shard manifests found")
    report_paths: list[str] = []
    for shard_path in shards:
        shard = read_json(shard_path)
        shard_id = shard.get("shard_id")
        report_path = root / f"out/{shard_id}/exec_report.json"
        video_path = root / f"out/{shard_id}/{shard_id}.mp4"
        report_paths.append(str(report_path))
        if not report_path.is_file():
            errors.append(f"missing report for {shard_id}")
            continue
        report = read_json(report_path)
        missing_report_keys = sorted(REQUIRED_REPORT_KEYS - set(report))
        if missing_report_keys:
            errors.append(f"{shard_id} report missing keys: {', '.join(missing_report_keys)}")
        if report.get("schema") != REPORT_SCHEMA:
            errors.append(f"{shard_id} report schema mismatch")
        if report.get("status") != "success":
            errors.append(f"{shard_id} is not successful")
        expected = [item.get("segment_id") for item in shard.get("segments") or []]
        actual = [item.get("segment_id") for item in report.get("segments") or []]
        if actual != expected:
            errors.append(f"{shard_id} report segments differ from the frozen shard")
        for item in report.get("segments") or []:
            missing_segment_keys = sorted(REQUIRED_SEGMENT_REPORT_KEYS - set(item))
            if missing_segment_keys:
                errors.append(
                    f"{shard_id}/{item.get('segment_id')} report missing keys: {', '.join(missing_segment_keys)}"
                )
            if item.get("status") != "resolved":
                errors.append(f"{shard_id}/{item.get('segment_id')} is not resolved")
            if item.get("understanding_mode") not in {"omni_grounded", "degraded_local_repin"}:
                errors.append(f"{shard_id}/{item.get('segment_id')} understanding_mode is invalid")
        if report.get("unresolved"):
            errors.append(f"{shard_id} has unresolved items")
        checks = report.get("qa_checks")
        if not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values()):
            errors.append(f"{shard_id} QA checks are incomplete or failed")
        if not video_path.is_file() or video_path.stat().st_size == 0:
            errors.append(f"missing or empty video for {shard_id}")
    final_video = root / "full/commentary.mp4"
    final_qa = root / "full/orchestrator_qa_full.json"
    if require_final:
        if not final_video.is_file() or final_video.stat().st_size == 0:
            errors.append("full/commentary.mp4 is missing or empty")
        if not final_qa.is_file():
            errors.append("full/orchestrator_qa_full.json is missing")
        else:
            qa = read_json(final_qa)
            if qa.get("schema") != QA_SCHEMA:
                errors.append("final QA schema mismatch")
            checks = qa.get("checks")
            if not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values()):
                errors.append("final QA checks are incomplete or failed")
            if qa.get("overall_pass") is not True:
                errors.append("final QA overall_pass is not true")
    return {
        "valid": not errors,
        "project_root": str(root),
        "shard_count": len(shards),
        "reports": report_paths,
        "final_video": str(final_video),
        "final_qa": str(final_qa),
        "errors": errors,
    }
