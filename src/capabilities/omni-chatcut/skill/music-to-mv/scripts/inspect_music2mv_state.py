#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def newest(paths):
    paths = [path for path in paths if path.exists()]
    return str(max(paths, key=lambda path: path.stat().st_mtime)) if paths else ""


def preferred_audio(paths):
    def score(path):
        lowered_parts = {part.lower() for part in path.parts}
        name = path.stem.lower()
        value = 0
        if any(token in name for token in ("source", "original", "music", "song", "track")):
            value += 8
        if name.startswith("segment_") or name.startswith("clip_"):
            value -= 12
        if lowered_parts & {"segments", "clips", "generated", "execution", "normalized", "raw"}:
            value -= 8
        value -= len(path.parts) * 0.05
        value += min(path.stat().st_size, 100_000_000) / 100_000_000
        return value, path.stat().st_mtime

    paths = [
        path
        for path in paths
        if path.exists()
        and not (
            "work" in {part.lower() for part in path.parts}
            and ({"clips", "segments"} & {part.lower() for part in path.parts})
        )
    ]
    return str(max(paths, key=score)) if paths else ""


def read_text(path, limit=2_000_000):
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def iter_files(root):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def is_caption(path):
    if path.suffix.lower() not in {".md", ".txt"}:
        return False
    text = read_text(path).lower()
    required = ("whole-track overview", "structure timeline", "lyrics timeline")
    return all(item in text for item in required)


def classify_json(path, data):
    if not isinstance(data, dict):
        return None
    keys = set(data)
    if {"video", "style_bible", "cast", "scenes", "segments"}.issubset(keys):
        return "storyboard"
    if {"structure", "lyrics"}.issubset(keys):
        return "music_map"
    if "quality_pass" in data and "valid" in data and "metrics" in data:
        return "storyboard_validation"
    if data.get("schema") == "author-mv/continuity-report/v1":
        return "continuity_report"
    if data.get("schema") == "music2mv/music-caption-run/v1":
        return "caption_run_state"
    if path.name == "state.json" and ("assets" in data or "segments" in data or "tasks" in data):
        return "execution_state"
    if "generation_manifest" in path.name or path.name == "generation_manifest.json":
        return "generation_manifest"
    if "final_qc" in path.name or ("technical" in path.name and "qc" in path.name):
        return "technical_qc"
    if data.get("schema") in {
        "music2mv/project/v1",
        "qwen-mm-plugins-music2mv/project/v1",
    }:
        return "orchestration_manifest"
    return None


def storyboard_facts(path):
    data = read_json(path) or {}
    direction = data.get("creative_direction", {})
    media_form = direction.get("media_form", "")
    assembly_counts = {}
    segments = data.get("segments", [])
    one_shot_segments = 0
    for segment in segments:
        mode = segment.get("assembly_mode", "missing")
        assembly_counts[mode] = assembly_counts.get(mode, 0) + 1
        subshots = segment.get("sub_shots", [])
        windows = segment.get("shot_windows", [])
        if len(subshots) == 1 and len(windows) == 1 and mode == "single_take_i2v":
            one_shot_segments += 1
    return {
        "path": str(path),
        "media_form": media_form,
        "has_creative_direction": bool(direction),
        "assembly_mode_counts": assembly_counts,
        "segment_count": len(segments),
        "subshot_count": sum(len(segment.get("sub_shots", [])) for segment in segments),
        "one_shot_request_contract": bool(segments) and one_shot_segments == len(segments),
        "grouped_segment_count": len(segments) - one_shot_segments,
    }


def caption_run_facts(path, project_root):
    data = read_json(path) or {}
    recorded_source = data.get("source_audio", "")
    source_path = Path(recorded_source).expanduser() if recorded_source else None
    if source_path and not source_path.is_absolute():
        source_path = project_root / source_path
    source_readable = bool(source_path and source_path.is_file())
    status = data.get("status", "")
    return {
        "path": str(path),
        "status": status,
        "phase": data.get("phase", ""),
        "source_audio": str(source_path) if source_path else "",
        "source_audio_readable": source_readable,
        "resumable": status in {"running", "failed"} and source_readable,
        "last_error": data.get("last_error", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Inspect a project and recommend the next Music2MV stage")
    parser.add_argument("project_dir")
    parser.add_argument("--output")
    args = parser.parse_args()

    root = Path(args.project_dir).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Project directory does not exist: {root}")

    categories = {
        "audio": [],
        "caption": [],
        "caption_run_state": [],
        "music_map": [],
        "storyboard": [],
        "storyboard_validation": [],
        "continuity_report": [],
        "execution_state": [],
        "generation_manifest": [],
        "technical_qc": [],
        "orchestration_manifest": [],
        "final_mv": [],
    }
    parsed_json = {}

    for path in iter_files(root):
        suffix = path.suffix.lower()
        if suffix in AUDIO_EXTENSIONS:
            categories["audio"].append(path)
        elif suffix in {".md", ".txt"}:
            lowered = path.name.lower()
            if is_caption(path):
                categories["caption"].append(path)
        elif suffix == ".json":
            data = read_json(path)
            parsed_json[path] = data
            kind = classify_json(path, data)
            if kind:
                categories[kind].append(path)
        elif suffix in {".mp4", ".mov"}:
            lowered = path.name.lower()
            if "final" in lowered or "_mv" in lowered or lowered.startswith("mv"):
                if not any(part.lower() in {"segments", "raw", "normalized"} for part in path.parts):
                    categories["final_mv"].append(path)

    primary = {key: newest(value) for key, value in categories.items()}
    primary["audio"] = preferred_audio(categories["audio"])
    all_paths = {key: [str(path) for path in sorted(value)] for key, value in categories.items()}
    blockers = []
    warnings = []
    recommended_stage = "blocked_missing_input"

    storyboard_path = Path(primary["storyboard"]) if primary["storyboard"] else None
    storyboard = storyboard_facts(storyboard_path) if storyboard_path else {}
    validation = read_json(Path(primary["storyboard_validation"])) if primary["storyboard_validation"] else None
    continuity = read_json(Path(primary["continuity_report"])) if primary["continuity_report"] else None
    caption_run_path = Path(primary["caption_run_state"]) if primary["caption_run_state"] else None
    caption_run = caption_run_facts(caption_run_path, root) if caption_run_path else {}

    if primary["final_mv"]:
        recommended_stage = "complete_or_review"
        if storyboard_path and not storyboard.get("has_creative_direction"):
            warnings.append("completed_with_legacy_storyboard_missing_creative_direction")
        if storyboard_path and validation is None:
            warnings.append("completed_without_storyboard_validation_report")
        if storyboard_path and continuity is None:
            warnings.append("completed_without_continuity_report")
        if not primary["technical_qc"]:
            warnings.append("completed_without_technical_qc")
    elif primary["execution_state"]:
        recommended_stage = "execute_resume"
    elif storyboard_path:
        if not storyboard.get("has_creative_direction"):
            recommended_stage = "author_upgrade_needed"
            blockers.append("storyboard_missing_creative_direction")
        elif not storyboard.get("one_shot_request_contract"):
            recommended_stage = "author_fix_needed"
            blockers.append("storyboard_groups_multiple_shots_per_provider_request")
        elif validation is None:
            recommended_stage = "author_validate_needed"
            blockers.append("storyboard_validation_report_missing")
        elif continuity is None:
            recommended_stage = "author_validate_needed"
            blockers.append("continuity_report_missing")
        elif not validation.get("valid"):
            recommended_stage = "author_fix_needed"
            blockers.append("authoring_validator_failed")
        elif not continuity.get("pass"):
            recommended_stage = "author_fix_needed"
            blockers.append("authoring_continuity_failed")
        elif not validation.get("quality_pass"):
            recommended_stage = "author_review_needed"
            warnings.append("storyboard_validation_pass_false")
        elif not primary["audio"]:
            recommended_stage = "storyboard_complete_missing_audio_for_execution"
            blockers.append("source_audio_missing")
        elif storyboard.get("media_form") != "live_action":
            recommended_stage = "storyboard_complete_no_compatible_executor"
            blockers.append(f"unsupported_executor_media_form:{storyboard.get('media_form') or 'missing'}")
        else:
            recommended_stage = "execute_ready"
    elif primary["caption"] or primary["music_map"]:
        recommended_stage = "author_ready"
    elif caption_run and caption_run.get("status") in {"running", "failed"}:
        if caption_run.get("resumable"):
            recommended_stage = "caption_resume"
        else:
            recommended_stage = "caption_resume_missing_source_audio"
            blockers.append("caption_run_source_audio_missing")
    elif primary["audio"]:
        recommended_stage = "caption_ready"

    result = {
        "schema": "music2mv/state-inspection/v1",
        "project_root": str(root),
        "recommended_stage": recommended_stage,
        "blockers": blockers,
        "warnings": warnings,
        "primary_artifacts": primary,
        "caption_run": caption_run,
        "storyboard": storyboard,
        "authoring_validation": {
            "valid": validation.get("valid") if validation else None,
            "shot_count": validation.get("metrics", {}).get("shot_count") if validation else None,
            "shot_type_runtime": validation.get("metrics", {}).get("shot_type_runtime", {}) if validation else {},
            "continuity_pass": continuity.get("pass") if continuity else None,
            "continuity_issue_count": continuity.get("issue_count") if continuity else None,
        },
        "all_candidates": all_paths,
    }
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
