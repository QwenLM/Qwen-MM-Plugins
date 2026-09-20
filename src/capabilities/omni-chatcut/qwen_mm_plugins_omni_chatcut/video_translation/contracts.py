"""Plan and delivery validation for video translation."""

from __future__ import annotations

import hashlib
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from shared.syscmd import find_tool
from shared.video import probe_media

from .project import (
    PLAN_SCHEMA,
    QA_SCHEMA,
    REPORT_SCHEMA,
    REVIEW_SCHEMA,
    VAD_SCHEMA,
    file_sha256,
    project_duration,
    read_json,
)


def _estimated_spoken_duration(text: str, language: str) -> float:
    """Estimate delivery time for cross-language matching, not exact TTS timing."""
    language = language.lower().split("-")[0]
    han_count = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    if language == "zh" and han_count:
        return max(0.25, han_count / 4.2)
    if language == "ja":
        japanese_count = len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9d\u3400-\u9fff\uf900-\ufaff]", text))
        if japanese_count:
            return max(0.25, japanese_count / 4.2)
    words = re.findall(r"[\w']+", text, flags=re.UNICODE)
    if language == "ko":
        return max(0.25, len(words) / 3.2)
    return max(0.25, len(words) / 2.6)


def validate_plan(project_dir: str, plan_path: str | None = None) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    path = Path(plan_path).expanduser().resolve() if plan_path else root / "plan/translation_plan.json"
    errors: list[str] = []
    try:
        plan = read_json(path)
    except (OSError, ValueError) as exc:
        return {"valid": False, "plan_path": str(path), "segment_count": 0, "errors": [str(exc)]}
    try:
        manifest = read_json(root / "project.json")
        duration = project_duration(root, manifest)
    except (OSError, ValueError) as exc:
        return {"valid": False, "plan_path": str(path), "segment_count": 0, "errors": [str(exc)]}

    artifacts = manifest.get("artifacts") or {}
    transcript_path = root / str(artifacts.get("transcript") or "analysis/transcript.json")
    vad_path = root / str(artifacts.get("vad") or "analysis/vad.json")
    try:
        transcript = read_json(transcript_path)
    except (OSError, ValueError) as exc:
        return {
            "valid": False,
            "plan_path": str(path),
            "segment_count": 0,
            "errors": [f"accepted transcript is missing or invalid: {exc}"],
        }
    try:
        vad = read_json(vad_path)
    except (OSError, ValueError) as exc:
        return {
            "valid": False,
            "plan_path": str(path),
            "segment_count": 0,
            "errors": [f"accepted VAD evidence is missing or invalid: {exc}"],
        }

    if plan.get("schema") != PLAN_SCHEMA:
        errors.append(f"schema must be {PLAN_SCHEMA}")
    if plan.get("source_movie") != manifest.get("source_movie"):
        errors.append("plan source_movie differs from project.json")
    if plan.get("source_sha256") != manifest.get("source_sha256"):
        errors.append("plan source_sha256 differs from project.json")
    if not isinstance(plan.get("source_language"), str) or not plan["source_language"].strip():
        errors.append("plan source_language is required")
    configured_source_language = manifest.get("source_language")
    transcript_source_language = transcript.get("source_language")
    if configured_source_language == "auto":
        if not isinstance(transcript_source_language, str) or not transcript_source_language.strip():
            errors.append("transcript source_language must contain the detected language")
        elif plan.get("source_language") != transcript_source_language:
            errors.append("plan source_language differs from the detected transcript language")
    else:
        if plan.get("source_language") != configured_source_language:
            errors.append("plan source_language differs from project.json")
        if transcript_source_language != configured_source_language:
            errors.append("transcript source_language differs from project.json")
    if plan.get("target_language") != manifest.get("target_language"):
        errors.append("plan target_language differs from project.json")
    if plan.get("transcript_sha256") != file_sha256(transcript_path):
        errors.append("plan transcript_sha256 differs from the accepted transcript")
    if plan.get("vad_sha256") != file_sha256(vad_path):
        errors.append("plan vad_sha256 differs from the accepted VAD evidence")
    for field in ("source_movie", "source_sha256"):
        if transcript.get(field) != manifest.get(field):
            errors.append(f"transcript {field} differs from project.json")
    if vad.get("schema") != VAD_SCHEMA:
        errors.append(f"VAD schema must be {VAD_SCHEMA}")
    for field in ("source_movie", "source_sha256"):
        if vad.get(field) != manifest.get(field):
            errors.append(f"VAD {field} differs from project.json")
    vad_duration = vad.get("duration_sec")
    if (
        not isinstance(vad_duration, (int, float))
        or isinstance(vad_duration, bool)
        or not math.isfinite(vad_duration)
        or abs(float(vad_duration) - duration) > 0.35
    ):
        errors.append("VAD duration_sec differs from the source duration")

    vad_segments = vad.get("segments")
    if not isinstance(vad_segments, list) or not vad_segments:
        errors.append("VAD segments must be a non-empty array")
        vad_segments = []
    normalized_vad: list[tuple[float, float]] = []
    vad_previous_end = -1.0
    for index, interval in enumerate(vad_segments):
        if not isinstance(interval, dict):
            errors.append(f"VAD segments[{index}] must be an object")
            continue
        start = interval.get("start_sec", interval.get("start_time"))
        end = interval.get("end_sec", interval.get("end_time"))
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or end > duration + 0.05
            or start < vad_previous_end
        ):
            errors.append(f"VAD segments[{index}] has an invalid interval")
            continue
        normalized_vad.append((float(start), float(end)))
        vad_previous_end = float(end)

    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("segments must be a non-empty array")
        segments = []
    transcript_segments = transcript.get("segments")
    if not isinstance(transcript_segments, list) or not transcript_segments:
        errors.append("transcript segments must be a non-empty array")
        transcript_segments = []
    transcript_by_id: dict[str, dict[str, Any]] = {}
    transcript_ids: list[str] = []
    transcript_previous_end = -1.0
    evidence_diagnostics: list[dict[str, Any]] = []
    for index, item in enumerate(transcript_segments, start=1):
        prefix = f"transcript.segments[{index - 1}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        segment_id = item.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip() or segment_id in transcript_by_id:
            errors.append(f"{prefix}.segment_id must be unique and non-empty")
            continue
        start = item.get("start_sec")
        end = item.get("end_sec")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or end > duration + 0.05
            or start < transcript_previous_end
        ):
            errors.append(f"{prefix} has an invalid or overlapping interval")
            continue
        if not isinstance(item.get("speaker"), str) or not item["speaker"].strip():
            errors.append(f"{prefix}.speaker is required")
        if not isinstance(item.get("source_text"), str) or not item["source_text"].strip():
            errors.append(f"{prefix}.source_text is required")
        speech_overlap = sum(max(0.0, min(float(end), b) - max(float(start), a)) for a, b in normalized_vad)
        vad_overlap_ratio = speech_overlap / (float(end) - float(start))
        evidence_diagnostics.append(
            {
                "segment_id": segment_id,
                "vad_overlap_ratio": round(vad_overlap_ratio, 3),
                "review_recommended": vad_overlap_ratio < 0.35,
            }
        )
        transcript_by_id[segment_id] = item
        transcript_ids.append(segment_id)
        transcript_previous_end = float(end)

    previous_end = -1.0
    seen_ids: set[str] = set()
    consumed_source_ids: list[str] = []
    timing_diagnostics = []
    for index, segment in enumerate(segments, start=1):
        prefix = f"segments[{index - 1}]"
        expected = f"DUB_{index:04d}"
        if not isinstance(segment, dict):
            errors.append(f"{prefix} must be an object")
            continue
        segment_id = segment.get("segment_id")
        if segment_id != expected:
            errors.append(f"{prefix}.segment_id must be {expected}")
        if segment_id in seen_ids:
            errors.append(f"{prefix}.segment_id is duplicated")
        seen_ids.add(str(segment_id))
        for field in ("speaker", "source_text", "translated_text"):
            if not isinstance(segment.get(field), str) or not segment[field].strip():
                errors.append(f"{prefix}.{field} is required")
        source_segment_ids = segment.get("source_segment_ids")
        if (
            not isinstance(source_segment_ids, list)
            or not source_segment_ids
            or any(not isinstance(item, str) or not item for item in source_segment_ids)
        ):
            errors.append(f"{prefix}.source_segment_ids must be a non-empty string array")
            source_segment_ids = []
        source_items = [transcript_by_id[item] for item in source_segment_ids if item in transcript_by_id]
        if len(source_items) != len(source_segment_ids):
            errors.append(f"{prefix}.source_segment_ids contains an unknown transcript segment")
        elif source_items:
            if any(item.get("speaker") != segment.get("speaker") for item in source_items):
                errors.append(f"{prefix}.source_segment_ids must all belong to the dubbing speaker")
            if len(source_items) > 1 and not str(segment.get("merge_reason") or "").strip():
                errors.append(f"{prefix}.merge_reason is required when source segments are merged")
            consumed_source_ids.extend(source_segment_ids)
        start = segment.get("start_sec")
        end = segment.get("end_sec")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(start)
            or not math.isfinite(end)
        ):
            errors.append(f"{prefix} start_sec/end_sec must be numbers")
            continue
        interval_valid = not (start < 0 or end <= start or end > duration + 0.05)
        if not interval_valid:
            errors.append(f"{prefix} has an invalid source interval")
        if start < previous_end:
            errors.append(f"{prefix} overlaps the previous segment")
        previous_end = max(previous_end, float(end))
        if (
            interval_valid
            and source_items
            and (
                abs(float(start) - float(source_items[0]["start_sec"])) > 0.05
                or abs(float(end) - float(source_items[-1]["end_sec"])) > 0.05
            )
        ):
            errors.append(f"{prefix} timing must span its source_segment_ids")
        if interval_valid and isinstance(segment.get("translated_text"), str) and segment["translated_text"].strip():
            estimated = _estimated_spoken_duration(segment["translated_text"], str(plan.get("target_language") or ""))
            timing_diagnostics.append(
                {
                    "segment_id": segment_id,
                    "target_estimated_sec": round(estimated, 3),
                    "nominal_slot_sec": round(float(end) - float(start), 3),
                    "estimated_nominal_speedup": round(estimated / (float(end) - float(start)), 3),
                }
            )
        reference = segment.get("reference") or {}
        if not isinstance(reference, dict):
            errors.append(f"{prefix}.reference must be an object")
        else:
            ref_start = reference.get("start_sec", start)
            ref_end = reference.get("end_sec", end)
            ref_segment_ids = reference.get("source_segment_ids")
            selection_reason = reference.get("selection_reason")
            if (
                not isinstance(ref_segment_ids, list)
                or not ref_segment_ids
                or any(not isinstance(item, str) or not item for item in ref_segment_ids)
            ):
                errors.append(f"{prefix}.reference.source_segment_ids must be a non-empty string array")
                ref_segment_ids = []
            if not isinstance(selection_reason, str) or not selection_reason.strip():
                errors.append(f"{prefix}.reference.selection_reason is required")
            if (
                not isinstance(ref_start, (int, float))
                or isinstance(ref_start, bool)
                or not isinstance(ref_end, (int, float))
                or isinstance(ref_end, bool)
                or not math.isfinite(ref_start)
                or not math.isfinite(ref_end)
            ):
                errors.append(f"{prefix}.reference interval must be numeric")
            elif ref_start < 0 or ref_end <= ref_start or ref_end > duration + 0.05:
                errors.append(f"{prefix}.reference interval is invalid")
            else:
                reference_items = [transcript_by_id[item] for item in ref_segment_ids if item in transcript_by_id]
                if len(reference_items) != len(ref_segment_ids):
                    errors.append(f"{prefix}.reference.source_segment_ids contains an unknown transcript segment")
                elif reference_items:
                    if any(item.get("speaker") != segment.get("speaker") for item in reference_items):
                        errors.append(f"{prefix}.reference.source_segment_ids belongs to another speaker")
                    for left, right in zip(reference_items, reference_items[1:]):
                        if float(right["start_sec"]) - float(left["end_sec"]) > 1.2:
                            errors.append(f"{prefix}.reference source segments are more than 1.2 seconds apart")
                    if (
                        ref_start < float(reference_items[0]["start_sec"]) - 0.15
                        or ref_end > float(reference_items[-1]["end_sec"]) + 0.15
                    ):
                        errors.append(f"{prefix}.reference extends too far beyond its evidenced source segments")
                overlapping_speakers = {
                    item.get("speaker")
                    for item in transcript_segments
                    if isinstance(item, dict)
                    and isinstance(item.get("start_sec"), (int, float))
                    and not isinstance(item.get("start_sec"), bool)
                    and isinstance(item.get("end_sec"), (int, float))
                    and not isinstance(item.get("end_sec"), bool)
                    and float(item["start_sec"]) < float(ref_end)
                    and float(item["end_sec"]) > float(ref_start)
                }
                if segment.get("speaker") not in overlapping_speakers:
                    errors.append(f"{prefix}.reference has no evidenced speech from the selected speaker")
                if overlapping_speakers - {segment.get("speaker")}:
                    errors.append(f"{prefix}.reference overlaps speech from another speaker")
    if consumed_source_ids != transcript_ids:
        errors.append("plan source_segment_ids must cover every transcript segment exactly once and in order")
    return {
        "valid": not errors,
        "plan_path": str(path),
        "segment_count": len(segments),
        "evidence_diagnostics": evidence_diagnostics,
        "timing_diagnostics": timing_diagnostics,
        "errors": errors,
    }


def _decode_ok(path: Path) -> bool:
    completed = subprocess.run(
        [find_tool("ffmpeg"), "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        timeout=1800,
    )
    return completed.returncode == 0


def _stream_hash(path: Path, selector: str, *, decoded_audio: bool = False) -> str | None:
    command = [find_tool("ffmpeg"), "-v", "error", "-i", str(path), "-map", selector]
    if decoded_audio:
        command.extend(["-f", "s16le", "-acodec", "pcm_s16le", "-"])
    else:
        command.extend(["-c", "copy", "-f", "data", "-"])
    completed = subprocess.run(command, capture_output=True, timeout=1800)
    if completed.returncode != 0:
        return None
    return hashlib.sha256(completed.stdout).hexdigest()


def validate_delivery(project_dir: str) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    errors: list[str] = []
    video_path = root / "full/translated.mp4"
    report_path = root / "full/render_report.json"
    qa_path = root / "full/final_qa.json"
    review_path = root / "full/agent_review.json"
    if not video_path.is_file() or video_path.stat().st_size == 0:
        errors.append("full/translated.mp4 is missing or empty")
    if not report_path.is_file():
        errors.append("full/render_report.json is missing")
    if not qa_path.is_file():
        errors.append("full/final_qa.json is missing")
    if not review_path.is_file():
        errors.append("full/agent_review.json is missing")
    if errors:
        return {"valid": False, "final_video": str(video_path), "errors": errors}

    try:
        report = read_json(report_path)
        qa = read_json(qa_path)
        review = read_json(review_path)
        manifest = read_json(root / "project.json")
        plan_path = root / str(
            (manifest.get("artifacts") or {}).get("translation_plan") or "plan/translation_plan.json"
        )
        plan = read_json(plan_path)
    except (OSError, ValueError) as exc:
        return {"valid": False, "final_video": str(video_path), "errors": [str(exc)]}
    if report.get("schema") != REPORT_SCHEMA:
        errors.append("render report schema mismatch")
    if qa.get("schema") != QA_SCHEMA:
        errors.append("final QA schema mismatch")
    if review.get("schema") != REVIEW_SCHEMA:
        errors.append("agent review schema mismatch")
    if report.get("output_sha256") != file_sha256(video_path):
        errors.append("final video hash differs from render report")
    if report.get("source_movie") != manifest.get("source_movie"):
        errors.append("render report source_movie differs from project.json")
    if report.get("source_sha256") != manifest.get("source_sha256"):
        errors.append("render report source_sha256 differs from project.json")
    if report.get("plan_sha256") != file_sha256(plan_path):
        errors.append("translation plan changed after rendering")
    background_mode = report.get("background_mode", "include")
    if background_mode not in {"include", "omit"}:
        errors.append("render report has an invalid background_mode")
    elif background_mode == "omit" and report.get("premix_background") is not None:
        errors.append("render report retained a premix background while background_mode is omit")
    if review.get("output_sha256") != file_sha256(video_path):
        errors.append("agent review does not match the final video")
    if review.get("plan_sha256") != file_sha256(plan_path):
        errors.append("agent review does not match the translation plan")
    if not validate_plan(str(root), str(plan_path))["valid"]:
        errors.append("current translation plan is invalid")

    try:
        probe = probe_media(str(video_path))
        streams = probe.get("streams") or []
        duration = float((probe.get("format") or {}).get("duration") or 0.0)
        if not any(item.get("codec_type") == "video" for item in streams):
            errors.append("final video has no video stream")
        if not any(item.get("codec_type") == "audio" for item in streams):
            errors.append("final video has no audio stream")
        expected = float(report.get("source_duration_sec") or 0.0)
        if duration <= 0 or expected <= 0 or abs(duration - expected) > 0.35:
            errors.append("final duration differs from source duration")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ffprobe failed: {exc}")
    if not _decode_ok(video_path):
        errors.append("final video does not fully decode")
    source_path = Path(str(report.get("source_movie") or ""))
    if not source_path.is_file():
        errors.append("render report source_movie is missing")
    else:
        if file_sha256(source_path) != report.get("source_sha256"):
            errors.append("source movie content changed after rendering")
        source_video_hash = _stream_hash(source_path, "0:v:0")
        final_video_hash = _stream_hash(video_path, "0:v:0")
        if source_video_hash is None or final_video_hash is None:
            errors.append("failed to hash source or final video stream")
        elif source_video_hash != final_video_hash:
            errors.append("final video stream differs from the source video stream")
        source_audio_hash = _stream_hash(source_path, "0:a:0", decoded_audio=True)
        final_audio_hash = _stream_hash(video_path, "0:a:0", decoded_audio=True)
        if source_audio_hash is None or final_audio_hash is None:
            errors.append("failed to hash source or final audio stream")
        elif source_audio_hash == final_audio_hash:
            errors.append("final audio was not replaced")
    report_segments = report.get("segments")
    plan_segments = plan.get("segments")
    if not isinstance(report_segments, list) or not isinstance(plan_segments, list):
        errors.append("render report or translation plan has invalid segments")
    elif [item.get("segment_id") for item in report_segments if isinstance(item, dict)] != [
        item.get("segment_id") for item in plan_segments if isinstance(item, dict)
    ]:
        errors.append("render report segment accounting differs from the translation plan")
    checks = qa.get("checks")
    required_checks = {
        "decode",
        "streams",
        "duration",
        "segment_count",
        "premix_audio_format",
        "video_preserved",
        "audio_replaced",
    }
    if not isinstance(checks, dict) or not required_checks <= set(checks):
        errors.append("final QA is missing required checks")
    elif not all(checks.get(key) is True for key in required_checks):
        errors.append("one or more final QA checks failed")
    if qa.get("overall_pass") is not True:
        errors.append("final QA overall_pass is not true")
    review_checks = review.get("checks")
    required_review_checks = {"translation", "timing", "voice_reference", "natural_delivery", "mix"}
    if not isinstance(review_checks, dict) or not required_review_checks <= set(review_checks):
        errors.append("agent review is missing required quality checks")
    elif not all(review_checks.get(key) is True for key in required_review_checks):
        errors.append("one or more agent quality checks failed")
    expected_review_ids = (
        [item.get("segment_id") for item in plan_segments if isinstance(item, dict)]
        if isinstance(plan_segments, list)
        else []
    )
    if review.get("reviewed_segment_ids") != expected_review_ids:
        errors.append("agent review does not account for every dubbing segment")
    if not isinstance(review.get("issues"), list):
        errors.append("agent review issues must be an array")
    if review.get("overall_pass") is not True:
        errors.append("agent review overall_pass is not true")
    return {
        "valid": not errors,
        "final_video": str(video_path),
        "render_report": str(report_path),
        "final_qa": str(qa_path),
        "agent_review": str(review_path),
        "errors": errors,
    }
