#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

EPSILON = 0.02
ASSEMBLY_MODES = {"single_take_i2v"}
EDIT_ANCHOR_SOURCES = {
    "track_start",
    "section_boundary",
    "lyric_phrase_start",
    "lyric_phrase_end",
    "structure_internal_split",
}


def close(left, right):
    return abs(float(left) - float(right)) <= EPSILON


def main():
    parser = argparse.ArgumentParser(description="Validate a storyboard for identity-conditioned MV execution")
    parser.add_argument("storyboard")
    parser.add_argument("--report")
    args = parser.parse_args()
    data = json.loads(Path(args.storyboard).read_text(encoding="utf-8"))
    errors = []
    warnings = []

    def error(code, path, message):
        errors.append({"code": code, "path": path, "message": message})

    def warn(code, path, message):
        warnings.append({"code": code, "path": path, "message": message})

    for field in ("style_bible", "cast", "scenes", "segments"):
        if not data.get(field):
            error("missing_field", field, f"Required field {field} is missing or empty")

    media_form = data.get("creative_direction", {}).get("media_form")
    if media_form and media_form != "live_action":
        error(
            "unsupported_media_form",
            "creative_direction.media_form",
            f"This executor supports live_action only, not {media_form}",
        )

    cast_ids = {item.get("id") for item in data.get("cast", [])}
    scene_map = {item.get("id"): item for item in data.get("scenes", [])}
    scene_ids = set(scene_map)
    if None in cast_ids:
        error("cast_id", "cast", "Every cast member needs an id")
    if None in scene_ids:
        error("scene_id", "scenes", "Every scene needs an id")
    if len(cast_ids) != len(data.get("cast", [])):
        error("duplicate_cast", "cast", "Cast ids must be unique")
    if len(scene_ids) != len(data.get("scenes", [])):
        error("duplicate_scene", "scenes", "Scene ids must be unique")

    segments = sorted(data.get("segments", []), key=lambda item: int(item.get("index", -1)))
    editorial_plan = data.get("creative_direction", {}).get("editorial_plan", {})
    long_take_threshold = editorial_plan.get("long_take_threshold_sec", 8.0)
    for scene_id, scene in scene_map.items():
        for field in ("name", "setting", "lighting", "palette"):
            if not scene.get(field):
                error("scene_field", f"scenes[{scene_id}].{field}", f"Scene requires {field}")
    previous_end = 0.0
    global_indexes = set()
    unresolved_group_counts = []
    for position, segment in enumerate(segments):
        path = f"segments[{position}]"
        index = segment.get("index")
        if index is None:
            error("segment_index", path, "Segment index is required")
            continue
        if not close(segment.get("start_sec", -1), previous_end):
            error("segment_gap", path, f"Segment starts at {segment.get('start_sec')}, expected {previous_end}")
        start = float(segment.get("start_sec", 0))
        end = float(segment.get("end_sec", start))
        duration = float(segment.get("duration_sec", end - start))
        if not close(end - start, duration):
            error("segment_duration", path, "end_sec - start_sec must equal duration_sec")
        if not 1 <= duration <= 15:
            error(
                "shot_duration",
                f"{path}.duration_sec",
                "Each editorial shot must be 1-15 seconds for the shared audio-conditioned storyboard contract",
            )
        previous_end = end
        for scene_id in segment.get("scene_ids", []):
            if scene_id not in scene_ids:
                error("unknown_scene", f"{path}.scene_ids", f"Unknown scene id {scene_id}")
        for cast_id in segment.get("cast_present", []):
            if cast_id not in cast_ids:
                error("unknown_cast", f"{path}.cast_present", f"Unknown cast id {cast_id}")

        subshots = segment.get("sub_shots", [])
        shot_windows = sorted(segment.get("shot_windows", []), key=lambda item: float(item.get("t", [0])[0]))
        if len(subshots) != 1:
            error("one_shot_per_request", f"{path}.sub_shots", "Segment must contain exactly one editorial shot")
        mode = segment.get("assembly_mode")
        if mode not in ASSEMBLY_MODES:
            error(
                "assembly_mode",
                f"{path}.assembly_mode",
                f"assembly_mode must be one of {sorted(ASSEMBLY_MODES)}",
            )
        elif mode != "single_take_i2v":
            error(
                "assembly_edit_mismatch",
                f"{path}.assembly_mode",
                "A one-shot segment must use single_take_i2v",
            )
        if len(shot_windows) != 1:
            error("one_shot_per_request", f"{path}.shot_windows", "Segment must contain one full-duration shot window")
        sub_by_global = {}
        sub_previous = start
        for sub_index, subshot in enumerate(subshots):
            sub_path = f"{path}.sub_shots[{sub_index}]"
            global_index = subshot.get("global_index")
            if global_index is None or global_index in global_indexes:
                error("global_index", sub_path, "sub_shot.global_index must be globally unique")
            else:
                global_indexes.add(global_index)
                sub_by_global[global_index] = subshot
            if subshot.get("scene_id") not in scene_ids:
                error("unknown_scene", f"{sub_path}.scene_id", f"Unknown scene id {subshot.get('scene_id')}")
            for cast_id in subshot.get("cast_present", []):
                if cast_id not in cast_ids:
                    error("unknown_cast", f"{sub_path}.cast_present", f"Unknown cast id {cast_id}")
            if not close(subshot.get("start_sec", -1), sub_previous):
                error(
                    "subshot_gap", sub_path, f"Sub-shot starts at {subshot.get('start_sec')}, expected {sub_previous}"
                )
            sub_previous = float(subshot.get("end_sec", sub_previous))
            if (
                not close(subshot.get("start_sec", -1), start)
                or not close(subshot.get("end_sec", -1), end)
                or not close(subshot.get("duration_sec", -1), duration)
            ):
                error(
                    "one_shot_per_request",
                    sub_path,
                    "The only editorial shot must exactly match the provider segment timing",
                )
            reference = subshot.get("reference_image", {})
            if not reference.get("cast_scene_table"):
                error(
                    "cast_scene_table",
                    f"{sub_path}.reference_image",
                    "cast_scene_table is required for timed prompting",
                )
            table_ids = {
                character.get("id") for character in reference.get("cast_scene_table", {}).get("characters", [])
            }
            missing_table_ids = [cast_id for cast_id in subshot.get("cast_present", []) if cast_id not in table_ids]
            if missing_table_ids:
                warn(
                    "cast_table_missing",
                    f"{sub_path}.reference_image.cast_scene_table.characters",
                    f"Timed prompt details are missing for cast ids {missing_table_ids}",
                )
            for character in reference.get("cast_scene_table", {}).get("characters", []):
                if str(character.get("id", "")).startswith("G") and not any(
                    character.get(field) not in (None, "") for field in ("count", "character_count", "number")
                ):
                    unresolved_group_counts.append(
                        {"segment_index": index, "sub_global_index": global_index, "cast_id": character.get("id")}
                    )
            sync = subshot.get("audio_sync", {})
            # Early authoring_schema_v4 producers used ``lyrics`` while the
            # executor standardized on singular ``lyric``. Both fields carry
            # the same exact text, so accept the legacy spelling as a lossless
            # execution-time alias.
            if sync.get("vocal_present") and "lyric" not in sync and "lyrics" not in sync:
                error("lyric", f"{sub_path}.audio_sync", "Vocal sub-shot needs exact lyric text")
            edit_anchor = sync.get("edit_anchor")
            if not isinstance(edit_anchor, dict):
                error(
                    "edit_anchor",
                    f"{sub_path}.audio_sync.edit_anchor",
                    "Every sub-shot needs a storyboard-declared edit anchor",
                )
            else:
                if edit_anchor.get("source") not in EDIT_ANCHOR_SOURCES:
                    error(
                        "edit_anchor",
                        f"{sub_path}.audio_sync.edit_anchor.source",
                        "Unknown storyboard edit-anchor source",
                    )
                anchor_time = edit_anchor.get("source_time_sec")
                if not isinstance(anchor_time, (int, float)):
                    error(
                        "edit_anchor",
                        f"{sub_path}.audio_sync.edit_anchor.source_time_sec",
                        "Edit anchor needs a source timestamp",
                    )
                elif abs(float(anchor_time) - float(subshot.get("start_sec", 0))) > 1.0:
                    error(
                        "edit_anchor_alignment",
                        f"{sub_path}.audio_sync.edit_anchor.source_time_sec",
                        "Shot start must stay within one second of the declared edit anchor",
                    )
                if (
                    not str(edit_anchor.get("evidence", "")).strip()
                    or not str(edit_anchor.get("selection_reason", "")).strip()
                ):
                    error(
                        "edit_anchor",
                        f"{sub_path}.audio_sync.edit_anchor",
                        "Edit anchor needs music-analysis or structure-interval evidence and a selection reason",
                    )
            shot_duration = float(subshot.get("duration_sec", 0) or 0)
            if isinstance(long_take_threshold, (int, float)) and shot_duration >= float(long_take_threshold) - EPSILON:
                stages = sync.get("internal_music_stages")
                if not str(subshot.get("long_take_rationale", "")).strip():
                    error(
                        "long_take_music_evidence",
                        f"{sub_path}.long_take_rationale",
                        "Long take requires explicit musical and visual rationale",
                    )
                if not isinstance(stages, list) or len(stages) < 2:
                    error(
                        "long_take_music_evidence",
                        f"{sub_path}.audio_sync.internal_music_stages",
                        "Long take requires at least two lyric- or structure-grounded internal music stages",
                    )
        if subshots and not close(sub_previous, end):
            error("subshot_coverage", path, "Sub-shots must cover the complete segment")

        window_previous = 0.0
        for window_index, shot_window in enumerate(shot_windows):
            window_path = f"{path}.shot_windows[{window_index}]"
            window = shot_window.get("t", [])
            if len(window) != 2:
                error("shot_window", window_path, "Shot window must contain [start, end]")
                continue
            if not close(window[0], window_previous):
                error("shot_window_gap", window_path, f"Shot window starts at {window[0]}, expected {window_previous}")
            window_previous = float(window[1])
            subshot = sub_by_global.get(shot_window.get("sub_global_index"))
            if not subshot:
                error("shot_window_subshot", window_path, "Shot window references an unknown sub-shot")
        if shot_windows and not close(window_previous, duration):
            error("shot_window_coverage", path, "shot_windows must cover the complete segment duration")
        if shot_windows:
            only_window = shot_windows[0].get("t", [])
            if len(only_window) != 2 or not close(only_window[0], 0) or not close(only_window[1], duration):
                error(
                    "one_shot_per_request",
                    f"{path}.shot_windows[0]",
                    "The only shot window must equal [0, segment.duration_sec]",
                )
        if subshots:
            shot = subshots[0]
            expected_scene_ids = [shot.get("scene_id")] if shot.get("scene_id") else []
            if segment.get("scene_ids", []) != expected_scene_ids:
                error("one_shot_per_request", f"{path}.scene_ids", "Segment scene_ids must contain only the shot scene")
            if segment.get("cast_present", []) != shot.get("cast_present", []):
                error("one_shot_per_request", f"{path}.cast_present", "Segment cast_present must equal the shot cast")
    target_duration = data.get("video", {}).get("duration_sec")
    if target_duration is not None and segments and not close(previous_end, target_duration):
        error("timeline_end", "segments", f"Timeline ends at {previous_end}, expected {target_duration}")
    if unresolved_group_counts:
        warn(
            "group_count_unresolved",
            "segments[].sub_shots[].reference_image.cast_scene_table.characters[]",
            f"{len(unresolved_group_counts)} group appearances have no explicit numeric count; timed prompts cannot enforce an exact total. Examples: {unresolved_group_counts[:10]}",
        )

    report = {
        "valid": not errors,
        "execution_mode": "one_editorial_shot_per_request",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "segment_count": len(segments),
            "subshot_count": len(global_indexes),
            "text_scene_count": len(scene_ids),
            "generated_keyframe_count": 0,
            "timeline_end_sec": previous_end,
        },
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(output + "\n", encoding="utf-8")
    print(output)
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
