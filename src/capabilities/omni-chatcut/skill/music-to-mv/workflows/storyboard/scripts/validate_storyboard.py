#!/usr/bin/env python3
"""Validate the streamlined Music2MV storyboard authoring handoff."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

MEDIA_FORMS = {"live_action", "2d_animation", "3d_animation", "mixed_media"}
SHOT_TYPES = {"dance", "narrative", "concept", "performance"}
ASSEMBLY_MODES = {"single_take_i2v"}
TRANSITIONS = {"cut", "dissolve", "fade_in", "match_cut"}
LINK_MODES = {"start", "continuous", "cutaway", "time_jump", "location_change", "concept_transform", "montage"}
EDIT_ANCHOR_SOURCES = {
    "track_start",
    "section_boundary",
    "lyric_phrase_start",
    "lyric_phrase_end",
    "structure_internal_split",
}
VISUAL_UNIT_SOURCE_KINDS = {"single_cue", "merged_cues", "instrumental"}
STYLE_FIELDS = (
    "overall_visual_style",
    "color_palette",
    "film_look",
    "mood",
    "composition_grammar",
    "camera_grammar",
    "lighting_grammar",
    "material_language",
    "production_design",
    "wardrobe_rules",
)
VISUAL_FIELDS = ("image_content", "composition", "lighting_color_material", "visible_change", "handoff_to_next")
ACTION_FIELDS = ("playable_verb", "start_physical_state", "end_physical_state")
MOVEMENT_FIELDS = ("dance_style", "movement_quality", "body_channels", "camera_protection")
PERFORMANCE_FIELDS = ("delivery_mode", "visible_performance", "intensity_change")


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def close(left, right, tolerance=0.035):
    try:
        return math.isclose(float(left), float(right), abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Validate a generation-ready Music2MV storyboard")
    parser.add_argument("storyboard")
    parser.add_argument("--report")
    parser.add_argument("--continuity-report")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--music-map", help="normalized music map used to verify verbatim section captions")
    args = parser.parse_args()

    source = Path(args.storyboard)
    data = json.loads(source.read_text(encoding="utf-8"))
    errors = []
    warnings = []
    continuity_issues = []

    def error(code, path, message, *, continuity=False):
        item = {"code": code, "path": path, "message": message}
        errors.append(item)
        if continuity:
            continuity_issues.append({**item, "severity": "error"})

    def warn(code, path, message, *, continuity=False):
        item = {"code": code, "path": path, "message": message}
        warnings.append(item)
        if continuity:
            continuity_issues.append({**item, "severity": "warning"})

    music_structure = None
    if args.music_map:
        music_map_path = Path(args.music_map)
        try:
            music_map = json.loads(music_map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            error("music_map", str(music_map_path), f"cannot read normalized music map: {exc}")
        else:
            music_structure = music_map.get("structure") if isinstance(music_map, dict) else None
            if not isinstance(music_structure, list) or not music_structure:
                error("music_map", str(music_map_path), "music map must contain a non-empty structure list")
                music_structure = None

    if not isinstance(data, dict):
        raise SystemExit("Storyboard root must be a JSON object")

    video = data.get("video")
    if not isinstance(video, dict):
        error("missing_video", "video", "video must be an object")
        video = {}
    duration = video.get("duration_sec")
    if not isinstance(duration, (int, float)) or duration <= 0:
        error("video_duration", "video.duration_sec", "video.duration_sec must be positive")
        duration = 0.0
    duration = float(duration)

    direction = data.get("creative_direction")
    if not isinstance(direction, dict):
        error("creative_direction", "creative_direction", "creative_direction must be an object")
        direction = {}
    media_form = direction.get("media_form")
    if media_form not in MEDIA_FORMS:
        error("media_form", "creative_direction.media_form", f"media_form must be one of {sorted(MEDIA_FORMS)}")

    editorial = direction.get("editorial_plan", {})
    if not isinstance(editorial, dict):
        error("editorial_plan", "creative_direction.editorial_plan", "editorial_plan must be an object when present")
        editorial = {}
    long_take_threshold = editorial.get("long_take_threshold_sec", 8.0)
    if not isinstance(long_take_threshold, (int, float)) or long_take_threshold <= 0:
        error(
            "long_take_threshold",
            "creative_direction.editorial_plan.long_take_threshold_sec",
            "long_take_threshold_sec must be positive",
        )
        long_take_threshold = 8.0

    treatment = direction.get("whole_film_treatment")
    if not isinstance(treatment, dict):
        error(
            "whole_film_treatment",
            "creative_direction.whole_film_treatment",
            "whole_film_treatment must be an object",
        )
        treatment = {}
    for field in ("core_premise", "driving_thread", "visual_world_logic"):
        if not nonempty(treatment.get(field)):
            error(
                "whole_film_treatment",
                f"creative_direction.whole_film_treatment.{field}",
                f"whole_film_treatment requires {field}",
            )
    if "recurring_motifs" in treatment and not isinstance(treatment.get("recurring_motifs"), list):
        error(
            "whole_film_treatment",
            "creative_direction.whole_film_treatment.recurring_motifs",
            "recurring_motifs must be a list when present",
        )

    development_path = treatment.get("development_path")
    if not isinstance(development_path, list) or not development_path:
        error(
            "development_path",
            "creative_direction.whole_film_treatment.development_path",
            "development_path must be a non-empty list",
        )
        development_path = []
    development_by_id = {}
    previous_stage_end = 0.0
    for position, stage in enumerate(development_path):
        path = f"creative_direction.whole_film_treatment.development_path[{position}]"
        if not isinstance(stage, dict):
            error("development_stage", path, "development stage must be an object")
            continue
        stage_id = stage.get("id")
        if not nonempty(stage_id) or stage_id in development_by_id:
            error("development_stage", f"{path}.id", "development stage needs a unique non-empty id")
        else:
            development_by_id[stage_id] = stage
        stage_start = stage.get("start_sec")
        stage_end = stage.get("end_sec")
        if not close(stage_start, previous_stage_end):
            error("development_timeline", path, f"development stage must start at {previous_stage_end}")
        if (
            not isinstance(stage_start, (int, float))
            or not isinstance(stage_end, (int, float))
            or float(stage_end or 0) <= float(stage_start or 0)
        ):
            error("development_timeline", path, "development stage needs a positive time range")
        previous_stage_end = float(stage_end) if isinstance(stage_end, (int, float)) else previous_stage_end
        for field in ("visible_state", "development", "handoff"):
            if not nonempty(stage.get(field)):
                error("development_stage", f"{path}.{field}", f"development stage requires {field}")
    if development_path and not close(previous_stage_end, duration):
        error(
            "development_timeline",
            "creative_direction.whole_film_treatment.development_path",
            f"development path ends at {previous_stage_end}, expected {duration}",
        )

    visual_units = direction.get("visual_units")
    if not isinstance(visual_units, list) or not visual_units:
        error("visual_units", "creative_direction.visual_units", "visual_units must be a non-empty list")
        visual_units = []
    visual_unit_by_id = {}
    previous_unit_end = 0.0
    stage_ids_used_by_units = set()
    for position, unit in enumerate(visual_units):
        path = f"creative_direction.visual_units[{position}]"
        if not isinstance(unit, dict):
            error("visual_unit", path, "visual unit must be an object")
            continue
        unit_id = unit.get("id")
        if not nonempty(unit_id) or unit_id in visual_unit_by_id:
            error("visual_unit", f"{path}.id", "visual unit needs a unique non-empty id")
        else:
            visual_unit_by_id[unit_id] = unit
        start = unit.get("start_sec")
        end = unit.get("end_sec")
        if not close(start, previous_unit_end):
            error("visual_unit_timeline", path, f"visual unit must start at {previous_unit_end}")
        if (
            not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or float(end or 0) <= float(start or 0)
        ):
            error("visual_unit_timeline", path, "visual unit needs a positive time range")
        previous_unit_end = float(end) if isinstance(end, (int, float)) else previous_unit_end
        unit_start_value = float(start) if isinstance(start, (int, float)) else 0.0
        unit_end_value = float(end) if isinstance(end, (int, float)) else unit_start_value
        source_kind = unit.get("source_kind")
        if source_kind not in VISUAL_UNIT_SOURCE_KINDS:
            error(
                "visual_unit_source",
                f"{path}.source_kind",
                f"source_kind must be one of {sorted(VISUAL_UNIT_SOURCE_KINDS)}",
            )
        stage_id = unit.get("development_stage_id")
        if stage_id not in development_by_id:
            error("visual_unit_stage", f"{path}.development_stage_id", f"unknown development stage {stage_id}")
        else:
            stage_ids_used_by_units.add(stage_id)
        for field in ("structure_refs", "lyric_cues"):
            if not isinstance(unit.get(field), list):
                error("visual_unit_evidence", f"{path}.{field}", f"{field} must be a list")
        if not unit.get("structure_refs"):
            error("visual_unit_evidence", f"{path}.structure_refs", "visual unit needs overlapping structure evidence")
        section_evidence = unit.get("section_caption_evidence")
        if not isinstance(section_evidence, list) or not section_evidence:
            error(
                "section_caption_evidence",
                f"{path}.section_caption_evidence",
                "visual unit needs caption evidence for every overlapping structure section",
            )
            section_evidence = []
        evidence_labels = []
        evidence_indices = set()
        evidence_overlaps = []
        for evidence_position, evidence in enumerate(section_evidence):
            evidence_path = f"{path}.section_caption_evidence[{evidence_position}]"
            if not isinstance(evidence, dict):
                error("section_caption_evidence", evidence_path, "section caption evidence must be an object")
                continue
            structure_index = evidence.get("structure_index")
            if not isinstance(structure_index, int) or structure_index < 0:
                error(
                    "section_caption_evidence",
                    f"{evidence_path}.structure_index",
                    "structure_index must be a non-negative integer",
                )
            elif structure_index in evidence_indices:
                error(
                    "section_caption_evidence",
                    f"{evidence_path}.structure_index",
                    "structure_index must be unique within a visual unit",
                )
            else:
                evidence_indices.add(structure_index)
            for field in ("label", "caption", "design_application"):
                if not nonempty(evidence.get(field)):
                    error(
                        "section_caption_evidence",
                        f"{evidence_path}.{field}",
                        f"section caption evidence requires {field}",
                    )
            if nonempty(evidence.get("label")):
                evidence_labels.append(evidence["label"])
            section_start = evidence.get("start_sec")
            section_end = evidence.get("end_sec")
            overlap_start = evidence.get("overlap_start_sec")
            overlap_end = evidence.get("overlap_end_sec")
            if not all(
                isinstance(value, (int, float)) for value in (section_start, section_end, overlap_start, overlap_end)
            ):
                error(
                    "section_caption_evidence",
                    evidence_path,
                    "section and overlap start/end values must be numeric",
                )
            else:
                expected_overlap_start = max(unit_start_value, float(section_start))
                expected_overlap_end = min(unit_end_value, float(section_end))
                if float(section_end) <= float(section_start) or expected_overlap_end <= expected_overlap_start:
                    error(
                        "section_caption_evidence",
                        evidence_path,
                        "section caption evidence must overlap the visual unit",
                    )
                if not close(overlap_start, expected_overlap_start) or not close(overlap_end, expected_overlap_end):
                    error(
                        "section_caption_evidence",
                        evidence_path,
                        f"overlap must equal [{expected_overlap_start}, {expected_overlap_end}]",
                    )
                evidence_overlaps.append((float(overlap_start), float(overlap_end)))
            if music_structure is not None and isinstance(structure_index, int) and structure_index >= 0:
                if structure_index >= len(music_structure):
                    error(
                        "section_caption_source",
                        f"{evidence_path}.structure_index",
                        f"structure_index {structure_index} is outside the music map",
                    )
                else:
                    source_section = music_structure[structure_index]
                    if not isinstance(source_section, dict):
                        error(
                            "section_caption_source", evidence_path, "referenced music-map structure entry is invalid"
                        )
                    else:
                        if evidence.get("label") != source_section.get("label"):
                            error("section_caption_source", f"{evidence_path}.label", "label does not match music map")
                        if not close(section_start, source_section.get("start_sec")) or not close(
                            section_end, source_section.get("end_sec")
                        ):
                            error(
                                "section_caption_source",
                                evidence_path,
                                "section time range does not match music map",
                            )
                        if evidence.get("caption") != source_section.get("caption"):
                            error(
                                "section_caption_source",
                                f"{evidence_path}.caption",
                                "caption must be a verbatim copy of the music map",
                            )
        refs = unit.get("structure_refs") if isinstance(unit.get("structure_refs"), list) else []
        expected_labels = list(dict.fromkeys(evidence_labels))
        if section_evidence and refs != expected_labels:
            error(
                "section_caption_evidence",
                f"{path}.structure_refs",
                f"structure_refs must match evidence labels in order: {expected_labels}",
            )
        if evidence_overlaps:
            evidence_overlaps.sort()
            coverage_cursor = unit_start_value
            for overlap_start, overlap_end in evidence_overlaps:
                if not close(overlap_start, coverage_cursor):
                    error(
                        "section_caption_coverage",
                        f"{path}.section_caption_evidence",
                        f"section caption evidence must cover the unit from {coverage_cursor}",
                    )
                    break
                coverage_cursor = overlap_end
            if not close(coverage_cursor, unit_end_value):
                error(
                    "section_caption_coverage",
                    f"{path}.section_caption_evidence",
                    f"section caption evidence ends at {coverage_cursor}, expected {unit_end_value}",
                )
        cues = unit.get("lyric_cues") if isinstance(unit.get("lyric_cues"), list) else []
        if source_kind == "instrumental" and cues:
            error("visual_unit_evidence", f"{path}.lyric_cues", "instrumental visual unit must not contain lyric cues")
        if source_kind == "single_cue" and len(cues) != 1:
            error("visual_unit_evidence", f"{path}.lyric_cues", "single_cue visual unit must contain exactly one cue")
        if source_kind == "merged_cues":
            if len(cues) < 2:
                error("visual_unit_evidence", f"{path}.lyric_cues", "merged_cues visual unit needs at least two cues")
            if not nonempty(unit.get("grouping_reason")):
                error("visual_unit_grouping", f"{path}.grouping_reason", "merged cues need a grouping reason")
        cue_indices = []
        for cue_position, cue in enumerate(cues):
            cue_path = f"{path}.lyric_cues[{cue_position}]"
            if not isinstance(cue, dict):
                error("visual_unit_evidence", cue_path, "lyric cue must be an object")
                continue
            if not isinstance(cue.get("index"), int):
                error("visual_unit_evidence", f"{cue_path}.index", "lyric cue index must be an integer")
            else:
                cue_indices.append(cue["index"])
            if not isinstance(cue.get("start_sec"), (int, float)) or not isinstance(cue.get("end_sec"), (int, float)):
                error("visual_unit_evidence", cue_path, "lyric cue needs numeric start_sec and end_sec")
            elif float(cue["start_sec"]) < unit_start_value - 0.035 or float(cue["end_sec"]) > unit_end_value + 0.035:
                error("visual_unit_evidence", cue_path, "lyric cue must stay inside its visual unit")
            if not nonempty(cue.get("text")):
                error("visual_unit_evidence", f"{cue_path}.text", "lyric cue needs verbatim text")
        if (
            source_kind == "merged_cues"
            and cue_indices
            and cue_indices != list(range(cue_indices[0], cue_indices[0] + len(cue_indices)))
        ):
            error("visual_unit_grouping", f"{path}.lyric_cues", "merged lyric cue indices must be consecutive")
        if not nonempty(unit.get("visual_intent")):
            error("visual_unit", f"{path}.visual_intent", "visual unit requires visual_intent")
    if visual_units and not close(previous_unit_end, duration):
        error(
            "visual_unit_timeline",
            "creative_direction.visual_units",
            f"visual units end at {previous_unit_end}, expected {duration}",
        )
    unused_stages = sorted(set(development_by_id) - stage_ids_used_by_units)
    if unused_stages:
        error(
            "development_stage_usage",
            "creative_direction.visual_units",
            f"development stages have no visual units: {unused_stages}",
        )

    style = data.get("style_bible")
    if not isinstance(style, dict):
        error("style_bible", "style_bible", "style_bible must be an object")
        style = {}
    for field in STYLE_FIELDS:
        if not nonempty(style.get(field)):
            error("style_field", f"style_bible.{field}", f"style_bible.{field} is required")
    for field in ("recurring_elements", "prohibited_elements"):
        if field in style and not isinstance(style[field], list):
            error("style_field", f"style_bible.{field}", f"style_bible.{field} must be a list")

    cast = data.get("cast")
    if not isinstance(cast, list):
        error("cast", "cast", "cast must be a list")
        cast = []
    cast_by_id = {}
    for index, person in enumerate(cast):
        path = f"cast[{index}]"
        if not isinstance(person, dict):
            error("cast_entry", path, "cast entry must be an object")
            continue
        cast_id = person.get("id")
        if not nonempty(cast_id):
            error("cast_id", f"{path}.id", "cast id is required")
            continue
        if cast_id in cast_by_id:
            error("duplicate_cast", f"{path}.id", f"duplicate cast id {cast_id}")
        cast_by_id[cast_id] = person
        for field in ("role", "identity", "portrait_t2i_prompt"):
            if not nonempty(person.get(field)):
                error("cast_field", f"{path}.{field}", f"cast entry requires {field}")

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        error("scenes", "scenes", "scenes must be a non-empty list")
        scenes = []
    scene_by_id = {}
    for index, scene in enumerate(scenes):
        path = f"scenes[{index}]"
        if not isinstance(scene, dict):
            error("scene_entry", path, "scene entry must be an object")
            continue
        scene_id = scene.get("id")
        if not nonempty(scene_id):
            error("scene_id", f"{path}.id", "scene id is required")
            continue
        if scene_id in scene_by_id:
            error("duplicate_scene", f"{path}.id", f"duplicate scene id {scene_id}")
        scene_by_id[scene_id] = scene
        for field in ("name", "setting", "lighting", "palette"):
            if not nonempty(scene.get(field)):
                error("scene_field", f"{path}.{field}", f"scene requires {field}")
        wardrobe = scene.get("wardrobe", {})
        if not isinstance(wardrobe, dict):
            error("scene_wardrobe", f"{path}.wardrobe", "wardrobe must be an object keyed by cast id")
        elif any(cast_id not in cast_by_id for cast_id in wardrobe):
            error("scene_wardrobe", f"{path}.wardrobe", "wardrobe contains an unknown cast id")

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        error("segments", "segments", "segments must be a non-empty list")
        segments = []

    global_ids = set()
    used_visual_unit_ids = set()
    type_runtime = defaultdict(float)
    type_counts = Counter()
    transition_counts = Counter()
    previous_segment_end = 0.0
    previous_shot = None

    for segment_position, segment in enumerate(segments):
        path = f"segments[{segment_position}]"
        if not isinstance(segment, dict):
            error("segment_entry", path, "segment must be an object")
            continue
        if segment.get("index") != segment_position:
            error("segment_index", f"{path}.index", "segment index must equal array position")
        start = segment.get("start_sec")
        end = segment.get("end_sec")
        seg_duration = segment.get("duration_sec")
        if not close(start, previous_segment_end):
            error("segment_gap", path, f"segment must start at {previous_segment_end}")
        if not close(float(end or 0) - float(start or 0), seg_duration):
            error("segment_duration", path, "end_sec - start_sec must equal duration_sec")
        previous_segment_end = float(end or previous_segment_end)
        mode = segment.get("assembly_mode")
        if mode not in ASSEMBLY_MODES:
            error("assembly_mode", f"{path}.assembly_mode", f"assembly_mode must be one of {sorted(ASSEMBLY_MODES)}")

        for scene_id in segment.get("scene_ids", []):
            if scene_id not in scene_by_id:
                error("unknown_scene", f"{path}.scene_ids", f"unknown scene id {scene_id}")
        for cast_id in segment.get("cast_present", []):
            if cast_id not in cast_by_id:
                error("unknown_cast", f"{path}.cast_present", f"unknown cast id {cast_id}")

        subshots = segment.get("sub_shots")
        if not isinstance(subshots, list):
            error(
                "one_shot_per_request",
                f"{path}.sub_shots",
                "each provider segment must contain exactly one editorial shot",
            )
            subshots = []
        elif len(subshots) != 1:
            error(
                "one_shot_per_request",
                f"{path}.sub_shots",
                "each provider segment must contain exactly one editorial shot",
            )
        if mode != "single_take_i2v":
            error("assembly_edit_mismatch", f"{path}.assembly_mode", "one editorial shot requires single_take_i2v")
        sub_previous = float(start or 0)
        sub_by_global = {}

        for sub_position, shot in enumerate(subshots):
            shot_path = f"{path}.sub_shots[{sub_position}]"
            if not isinstance(shot, dict):
                error("subshot_entry", shot_path, "sub_shot must be an object")
                continue
            if shot.get("seg_index") != segment_position or shot.get("sub_index") != sub_position:
                error("subshot_index", shot_path, "seg_index and sub_index must match array position")
            shot_start = shot.get("start_sec")
            shot_end = shot.get("end_sec")
            shot_duration = shot.get("duration_sec")
            if not close(shot_start, sub_previous):
                error("subshot_gap", shot_path, f"sub_shot must start at {sub_previous}")
            if not close(float(shot_end or 0) - float(shot_start or 0), shot_duration):
                error("subshot_duration", shot_path, "end_sec - start_sec must equal duration_sec")
            if not close(shot_start, start) or not close(shot_end, end) or not close(shot_duration, seg_duration):
                error(
                    "one_shot_per_request",
                    shot_path,
                    "the only editorial shot must exactly match its provider segment timing",
                )
            sub_previous = float(shot_end or sub_previous)

            global_id = shot.get("global_index")
            if not isinstance(global_id, int) or global_id in global_ids:
                error("global_index", f"{shot_path}.global_index", "global_index must be a globally unique integer")
            else:
                global_ids.add(global_id)
                sub_by_global[global_id] = shot

            shot_type = shot.get("shot_type")
            if shot_type not in SHOT_TYPES:
                error("shot_type", f"{shot_path}.shot_type", f"shot_type must be one of {sorted(SHOT_TYPES)}")
            else:
                type_counts[shot_type] += 1
                type_runtime[shot_type] += float(shot_duration or 0)
            for field in ("shot_function", "shot_summary"):
                if not nonempty(shot.get(field)):
                    error("shot_field", f"{shot_path}.{field}", f"shot requires {field}")

            visual_unit_id = shot.get("visual_unit_id")
            unit = visual_unit_by_id.get(visual_unit_id)
            if unit is None:
                error("visual_unit_reference", f"{shot_path}.visual_unit_id", f"unknown visual unit {visual_unit_id}")
            else:
                used_visual_unit_ids.add(visual_unit_id)
                unit_start = unit.get("start_sec")
                unit_end = unit.get("end_sec")
                if (
                    isinstance(unit_start, (int, float))
                    and isinstance(unit_end, (int, float))
                    and (
                        float(shot_start or 0) < float(unit_start) - 0.035
                        or float(shot_end or 0) > float(unit_end) + 0.035
                    )
                ):
                    error("visual_unit_reference", shot_path, "shot must stay wholly inside its visual unit")

            scene_id = shot.get("scene_id")
            if scene_id not in scene_by_id:
                error("unknown_scene", f"{shot_path}.scene_id", f"unknown scene id {scene_id}")
            cast_present = shot.get("cast_present", [])
            if not isinstance(cast_present, list):
                error("cast_present", f"{shot_path}.cast_present", "cast_present must be a list")
                cast_present = []
            for cast_id in cast_present:
                if cast_id not in cast_by_id:
                    error("unknown_cast", f"{shot_path}.cast_present", f"unknown cast id {cast_id}")

            visual = shot.get("visual_design")
            if not isinstance(visual, dict):
                error("visual_design", f"{shot_path}.visual_design", "visual_design must be an object")
                visual = {}
            for field in VISUAL_FIELDS:
                if not nonempty(visual.get(field)):
                    error("visual_design", f"{shot_path}.visual_design.{field}", f"visual_design requires {field}")

            action = shot.get("action_design")
            if not isinstance(action, dict):
                error("action_design", f"{shot_path}.action_design", "action_design must be an object")
                action = {}
            for field in ACTION_FIELDS:
                if not nonempty(action.get(field)):
                    error("action_design", f"{shot_path}.action_design.{field}", f"action_design requires {field}")
            if not isinstance(action.get("action_steps"), list) or not action.get("action_steps"):
                error(
                    "action_steps", f"{shot_path}.action_design.action_steps", "action_steps must be a non-empty list"
                )

            if shot_type == "dance":
                movement = shot.get("movement_design")
                if not isinstance(movement, dict):
                    error("movement_design", f"{shot_path}.movement_design", "dance shot requires movement_design")
                    movement = {}
                for field in MOVEMENT_FIELDS:
                    if not nonempty(movement.get(field)):
                        error(
                            "movement_design",
                            f"{shot_path}.movement_design.{field}",
                            f"movement_design requires {field}",
                        )
                for field in ("action_stages", "accent_actions"):
                    if not isinstance(movement.get(field), list) or not movement.get(field):
                        error(
                            "movement_design",
                            f"{shot_path}.movement_design.{field}",
                            f"movement_design.{field} must be non-empty",
                        )
            if shot_type == "performance":
                performance = shot.get("performance_design")
                if not isinstance(performance, dict):
                    error(
                        "performance_design",
                        f"{shot_path}.performance_design",
                        "performance shot requires performance_design",
                    )
                    performance = {}
                for field in PERFORMANCE_FIELDS:
                    if not nonempty(performance.get(field)):
                        error(
                            "performance_design",
                            f"{shot_path}.performance_design.{field}",
                            f"performance_design requires {field}",
                        )

            camera = shot.get("camera")
            if not isinstance(camera, dict):
                error("camera", f"{shot_path}.camera", "camera must be an object")
                camera = {}
            for field in ("shot_size", "angle", "movement", "subject_vs_camera"):
                if not nonempty(camera.get(field)):
                    error("camera", f"{shot_path}.camera.{field}", f"camera requires {field}")
            geometry = shot.get("camera_geometry")
            if not isinstance(geometry, dict) or not nonempty(geometry.get("focus_subject")):
                error("camera_geometry", f"{shot_path}.camera_geometry", "camera_geometry.focus_subject is required")

            sync = shot.get("audio_sync")
            if not isinstance(sync, dict):
                error("audio_sync", f"{shot_path}.audio_sync", "audio_sync must be an object")
                sync = {}
            if sync.get("vocal_present") and not (nonempty(sync.get("lyric")) or nonempty(sync.get("lyrics"))):
                error("lyric", f"{shot_path}.audio_sync", "vocal shot requires exact lyric text")
            anchor = sync.get("edit_anchor")
            if not isinstance(anchor, dict):
                error("edit_anchor", f"{shot_path}.audio_sync.edit_anchor", "every shot needs an edit_anchor")
            else:
                if anchor.get("source") not in EDIT_ANCHOR_SOURCES:
                    error("edit_anchor", f"{shot_path}.audio_sync.edit_anchor.source", "unknown edit-anchor source")
                if not isinstance(anchor.get("source_time_sec"), (int, float)):
                    error(
                        "edit_anchor",
                        f"{shot_path}.audio_sync.edit_anchor.source_time_sec",
                        "source timestamp is required",
                    )
                elif abs(float(anchor["source_time_sec"]) - float(shot_start or 0)) > 1.0:
                    error(
                        "edit_anchor_alignment",
                        f"{shot_path}.audio_sync.edit_anchor",
                        "shot start must be within one second of its evidence",
                    )
                for field in ("evidence", "selection_reason"):
                    if not nonempty(anchor.get(field)):
                        error(
                            "edit_anchor",
                            f"{shot_path}.audio_sync.edit_anchor.{field}",
                            f"edit_anchor requires {field}",
                        )

            reference = shot.get("reference_image")
            table = reference.get("cast_scene_table") if isinstance(reference, dict) else None
            if not isinstance(table, dict):
                error(
                    "cast_scene_table", f"{shot_path}.reference_image.cast_scene_table", "cast_scene_table is required"
                )
                table = {}
            characters = table.get("characters", [])
            if not isinstance(characters, list):
                error(
                    "cast_scene_table",
                    f"{shot_path}.reference_image.cast_scene_table.characters",
                    "characters must be a list",
                )
                characters = []
            table_ids = {item.get("id") for item in characters if isinstance(item, dict)}
            missing = [cast_id for cast_id in cast_present if cast_id not in table_ids]
            if missing:
                error(
                    "cast_scene_table",
                    f"{shot_path}.reference_image.cast_scene_table.characters",
                    f"missing cast entries {missing}",
                )

            continuity = shot.get("continuity")
            if not isinstance(continuity, dict):
                error("continuity", f"{shot_path}.continuity", "continuity must be an object", continuity=True)
                continuity = {}
            link_mode = continuity.get("link_mode")
            if link_mode not in LINK_MODES:
                error(
                    "continuity_link",
                    f"{shot_path}.continuity.link_mode",
                    f"link_mode must be one of {sorted(LINK_MODES)}",
                    continuity=True,
                )
            if previous_shot is not None and not nonempty(continuity.get("link_reason")):
                error(
                    "continuity_link",
                    f"{shot_path}.continuity.link_reason",
                    "link_reason is required after the first shot",
                    continuity=True,
                )
            if previous_shot is not None and link_mode == "continuous":
                previous_continuity = previous_shot.get("continuity", {})
                for incoming, outgoing in (
                    ("prop_state_in", "prop_state_out"),
                    ("blocking_in", "blocking_out"),
                    ("action_in", "action_out"),
                    ("gaze_in", "gaze_out"),
                ):
                    if continuity.get(incoming) != previous_continuity.get(outgoing):
                        error(
                            "continuity_mismatch",
                            f"{shot_path}.continuity.{incoming}",
                            f"{incoming} must match previous {outgoing}",
                            continuity=True,
                        )
            previous_shot = shot

            transition = shot.get("transition_in", "cut")
            transition_counts[transition] += 1
            if transition not in TRANSITIONS:
                error("transition", f"{shot_path}.transition_in", f"transition must be one of {sorted(TRANSITIONS)}")
            if transition == "match_cut" and not nonempty(shot.get("transition_basis")):
                error(
                    "transition_basis", f"{shot_path}.transition_basis", "match_cut requires a visible matching basis"
                )

            if isinstance(shot_duration, (int, float)) and shot_duration >= float(long_take_threshold) - 0.035:
                rationale = shot.get("long_take_rationale") or shot.get("shot_design", {}).get("long_take_rationale")
                stages = sync.get("internal_music_stages")
                if not nonempty(rationale):
                    error("long_take", shot_path, "long take requires long_take_rationale")
                if not isinstance(stages, list) or len(stages) < 2:
                    error(
                        "long_take",
                        f"{shot_path}.audio_sync.internal_music_stages",
                        "long take requires at least two internal music stages",
                    )

        if not close(sub_previous, end):
            error("subshot_coverage", f"{path}.sub_shots", "sub_shots must cover the complete segment")

        windows = segment.get("shot_windows")
        if not isinstance(windows, list) or len(windows) != 1:
            error(
                "one_shot_per_request",
                f"{path}.shot_windows",
                "each provider segment must contain exactly one full-duration shot window",
            )
            windows = []
        local_previous = 0.0
        for window_position, window in enumerate(sorted(windows, key=lambda item: item.get("t", [0])[0])):
            window_path = f"{path}.shot_windows[{window_position}]"
            times = window.get("t", [])
            if not isinstance(times, list) or len(times) != 2:
                error("shot_window", f"{window_path}.t", "window must contain [local_start, local_end]")
                continue
            if not close(times[0], local_previous):
                error("shot_window_gap", window_path, f"window must start at local time {local_previous}")
            local_previous = float(times[1])
            if window.get("sub_global_index") not in sub_by_global:
                error(
                    "shot_window_reference", f"{window_path}.sub_global_index", "window references an unknown sub_shot"
                )
        if windows and not close(local_previous, seg_duration):
            error("shot_window_coverage", f"{path}.shot_windows", "windows must cover the complete segment")
        if windows:
            first_times = windows[0].get("t", [])
            if len(first_times) != 2 or not close(first_times[0], 0):
                error("one_shot_per_request", f"{path}.shot_windows[0]", "the only shot window must start at zero")
        if subshots:
            expected_scene_ids = [subshots[0].get("scene_id")] if subshots[0].get("scene_id") else []
            if segment.get("scene_ids", []) != expected_scene_ids:
                error("one_shot_per_request", f"{path}.scene_ids", "segment scene_ids must contain only the shot scene")
            if segment.get("cast_present", []) != subshots[0].get("cast_present", []):
                error("one_shot_per_request", f"{path}.cast_present", "segment cast_present must equal the shot cast")

    if segments and not close(previous_segment_end, duration):
        error("timeline_end", "segments", f"timeline ends at {previous_segment_end}, expected {duration}")
    unused_visual_units = sorted(set(visual_unit_by_id) - used_visual_unit_ids)
    if unused_visual_units:
        error("visual_unit_usage", "segments", f"visual units have no implementing shots: {unused_visual_units}")

    if args.artifact_dir:
        artifact_dir = Path(args.artifact_dir)
        blueprints = list(artifact_dir.glob("*_creative_blueprint.md")) if artifact_dir.is_dir() else []
        if not blueprints:
            error("missing_artifact", str(artifact_dir), "final handoff requires a creative blueprint")

    total_runtime = sum(type_runtime.values())
    shot_type_runtime = {
        key: round(type_runtime.get(key, 0.0) / total_runtime, 4) if total_runtime else 0.0
        for key in sorted(SHOT_TYPES)
    }
    report = {
        "schema": "author-mv/validation-report/v3",
        "valid": not errors,
        "quality_pass": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "duration_sec": duration,
            "segment_count": len(segments),
            "shot_count": len(global_ids),
            "shot_type_counts": dict(sorted(type_counts.items())),
            "shot_type_runtime": shot_type_runtime,
            "transition_counts": dict(sorted(transition_counts.items())),
            "development_stage_count": len(development_by_id),
            "visual_unit_count": len(visual_unit_by_id),
            "merged_visual_unit_count": sum(1 for unit in visual_units if unit.get("source_kind") == "merged_cues"),
        },
    }
    continuity_report = {
        "schema": "author-mv/continuity-report/v1",
        "pass": not any(item["severity"] == "error" for item in continuity_issues),
        "issue_count": len(continuity_issues),
        "issues": continuity_issues,
    }

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(rendered, encoding="utf-8")
    if args.continuity_report:
        Path(args.continuity_report).write_text(
            json.dumps(continuity_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(rendered, end="")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
