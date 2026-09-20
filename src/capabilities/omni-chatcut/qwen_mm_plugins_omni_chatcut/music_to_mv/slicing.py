"""Parse Omni structure lines and cut exact audio clips with ffmpeg."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

_TIMESTAMP_RE = r"(?:\d{2}:\d{2}:\d{2}|\d{2}:\d{2,3}),\d{3}"
_LINE_RE = re.compile(
    rf"^\[(?P<start>{_TIMESTAMP_RE})\s*-->\s*"
    rf"(?P<end>{_TIMESTAMP_RE})\]\s*"
    r'\[(?P<label>[a-z][a-z-]*)\]\s*"(?P<lyrics>.*)"\s*$'
)
_SAFE_LABEL_RE = re.compile(r"[^a-z0-9-]+")
SHORT_SEGMENT_MERGE_SEC = 1.0


def _timestamp_format(value: str) -> str:
    if value.count(":") == 1 and len(value.split(":", 1)[1].split(",", 1)[0]) == 3:
        return "MM:SSS,mmm"
    return "HH:MM:SS,mmm" if value.count(":") == 2 else "MM:SS,mmm"


def timestamp_to_seconds(value: str) -> float:
    """Convert supported strict or unambiguous Omni-drift timestamps to seconds."""
    parts = value.split(":")
    if len(parts) == 2:
        hours = "0"
        minutes, tail = parts
    elif len(parts) == 3:
        hours, minutes, tail = parts
    else:
        raise ValueError(f"invalid timestamp: {value}")
    seconds, millis = tail.split(",")
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise ValueError(f"invalid timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def seconds_to_timestamp(value: float, timestamp_format: str) -> str:
    """Round seconds to milliseconds and render in the selected structure format."""
    total_ms = max(0, round(value * 1000))
    total_seconds, millis = divmod(total_ms, 1000)
    total_minutes, seconds = divmod(total_seconds, 60)
    if timestamp_format == "MM:SS,mmm":
        return f"{total_minutes:02d}:{seconds:02d},{millis:03d}"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _probe_audio_duration(source: Path, ffprobe_path: str) -> float:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {completed.stderr.strip() or 'unknown error'}")
    try:
        duration = float(completed.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe did not return a valid audio duration") from exc
    if duration <= 0:
        raise ValueError(f"audio has non-positive duration: {duration}")
    return round(duration, 3)


def parse_structure_asr(text: str) -> list[dict[str, Any]]:
    """Parse structure/lyrics lines, normalize timestamp syntax, and validate the timeline."""
    segments: list[dict[str, Any]] = []
    source_timestamp_formats: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = _LINE_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"malformed structure line {line_number}: {raw_line}")
        start_timestamp = match.group("start")
        end_timestamp = match.group("end")
        start_format = _timestamp_format(start_timestamp)
        end_format = _timestamp_format(end_timestamp)
        source_timestamp_formats.update((start_format, end_format))
        start = timestamp_to_seconds(start_timestamp)
        end = timestamp_to_seconds(end_timestamp)
        duration = end - start
        if duration <= 0.0005:
            raise ValueError(f"segment duration on line {line_number} must be positive")
        if not segments and start > 0.0005:
            raise ValueError("the first segment must start at zero")
        if segments:
            previous_end = segments[-1]["end_sec"]
            if start < previous_end - 0.0005:
                raise ValueError(f"overlapping segment on line {line_number}")
            if start > previous_end + 0.0005:
                raise ValueError(f"timeline gap before line {line_number}")
        segments.append(
            {
                "index": len(segments),
                "timestamp_format": start_format,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(duration, 3),
                "label": match.group("label"),
                "lyrics": match.group("lyrics"),
            }
        )
    if not segments:
        raise ValueError("structure_text contains no segment lines")
    if len(source_timestamp_formats) == 1 and "MM:SSS,mmm" not in source_timestamp_formats:
        timestamp_format = next(iter(source_timestamp_formats))
    else:
        # Omni may prepend an hour field partway through a response and emit ``00:019,840``
        # for 19.840 seconds. Canonicalize these unambiguous spellings before slicing.
        timestamp_format = "HH:MM:SS,mmm"
    for segment in segments:
        segment["timestamp_format"] = timestamp_format
        segment["start_timestamp"] = seconds_to_timestamp(segment["start_sec"], timestamp_format)
        segment["end_timestamp"] = seconds_to_timestamp(segment["end_sec"], timestamp_format)
    return segments


def merge_short_segments(
    segments: list[dict[str, Any]],
    minimum_duration_sec: float = SHORT_SEGMENT_MERGE_SEC,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge micro-sections into an adjacent section and preserve diagnostic provenance."""
    merged = []
    for segment in segments:
        item = dict(segment)
        item["merged_from"] = [
            {
                "original_index": segment["index"],
                "start_sec": segment["start_sec"],
                "end_sec": segment["end_sec"],
                "duration_sec": segment["duration_sec"],
                "label": segment["label"],
                "lyrics": segment["lyrics"],
            }
        ]
        merged.append(item)
    anomalies = []
    while len(merged) > 1:
        short_index = next(
            (index for index, item in enumerate(merged) if item["duration_sec"] < minimum_duration_sec - 0.0005),
            None,
        )
        if short_index is None:
            break
        short = merged[short_index]
        if short_index == 0:
            neighbor_index = 1
        elif short_index == len(merged) - 1:
            neighbor_index = short_index - 1
        else:
            previous = merged[short_index - 1]
            following = merged[short_index + 1]
            if previous["label"] == short["label"]:
                neighbor_index = short_index - 1
            elif following["label"] == short["label"]:
                neighbor_index = short_index + 1
            else:
                neighbor_index = short_index - 1
        neighbor = merged[neighbor_index]
        ordered = [short, neighbor] if short["start_sec"] < neighbor["start_sec"] else [neighbor, short]
        nonempty_lyrics = [item["lyrics"].strip() for item in ordered if item["lyrics"].strip()]
        combined = {
            **neighbor,
            "start_sec": ordered[0]["start_sec"],
            "end_sec": ordered[-1]["end_sec"],
            "duration_sec": round(ordered[-1]["end_sec"] - ordered[0]["start_sec"], 3),
            "lyrics": " ".join(nonempty_lyrics),
            "merged_from": [source for item in ordered for source in item.get("merged_from", [])],
            "was_merged": True,
        }
        anomalies.append(
            {
                "type": "short_segment_merged",
                "original_index": short["merged_from"][0]["original_index"],
                "original_indices": [source["original_index"] for source in short.get("merged_from", [])],
                "original_label": short["label"],
                "original_duration_sec": short["duration_sec"],
                "merged_into_label": neighbor["label"],
                "merge_direction": "next" if neighbor_index > short_index else "previous",
            }
        )
        low, high = sorted((short_index, neighbor_index))
        merged[low : high + 1] = [combined]
    if len(merged) == 1 and merged[0]["duration_sec"] < minimum_duration_sec - 0.0005:
        anomalies.append(
            {
                "type": "short_segment_retained",
                "original_indices": [source["original_index"] for source in merged[0].get("merged_from", [])],
                "duration_sec": merged[0]["duration_sec"],
                "reason": "the complete audio or remaining analysis interval is shorter than the merge threshold",
            }
        )
    timestamp_format = merged[0]["timestamp_format"]
    for index, segment in enumerate(merged):
        segment["index"] = index
        segment["start_timestamp"] = seconds_to_timestamp(segment["start_sec"], timestamp_format)
        segment["end_timestamp"] = seconds_to_timestamp(segment["end_sec"], timestamp_format)
    return merged, anomalies


def _clip_name(index: int, label: str, output_format: str) -> str:
    safe_label = _SAFE_LABEL_RE.sub("-", label.lower()).strip("-") or "segment"
    return f"{index:03d}_{safe_label}.{output_format}"


def slice_audio_from_structure(
    audio_path: str,
    structure_text: str,
    output_dir: str,
    output_format: Literal["wav", "mp3", "flac"] = "wav",
    overwrite: bool = False,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
) -> dict[str, Any]:
    """Cut one clip per parsed segment and return a JSON-serializable manifest."""
    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"audio_path is not a readable file: {source}")
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    segments = parse_structure_asr(structure_text)
    audio_duration_sec = _probe_audio_duration(source, ffprobe_path)
    final_segment = segments[-1]
    if final_segment["start_sec"] >= audio_duration_sec - 0.0005:
        raise ValueError(
            "the final structure segment starts at or after the actual audio end; retry structure prediction"
        )
    timeline_clipped_to_audio_end = final_segment["end_sec"] > audio_duration_sec + 0.0005
    if timeline_clipped_to_audio_end:
        original_end_timestamp = final_segment["end_timestamp"]
        final_segment["end_timestamp"] = seconds_to_timestamp(audio_duration_sec, final_segment["timestamp_format"])
        final_segment["end_sec"] = audio_duration_sec
        final_segment["duration_sec"] = round(audio_duration_sec - final_segment["start_sec"], 3)
        final_segment["original_end_timestamp"] = original_end_timestamp
        final_segment["end_clipped_to_audio"] = True

    segments, structure_anomalies = merge_short_segments(segments)

    codec_args = {
        "wav": ["-c:a", "pcm_s16le"],
        "mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
        "flac": ["-c:a", "flac"],
    }[output_format]
    manifest: list[dict[str, Any]] = []
    for segment in segments:
        clip_path = destination / _clip_name(segment["index"], segment["label"], output_format)
        if clip_path.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite existing clip: {clip_path}")
        command = [
            ffmpeg_path,
            "-v",
            "error",
            "-y" if overwrite else "-n",
            "-ss",
            f"{segment['start_sec']:.3f}",
            "-i",
            str(source),
            "-t",
            f"{segment['duration_sec']:.3f}",
            "-vn",
            *codec_args,
            str(clip_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed for segment {segment['index']}: {completed.stderr.strip() or 'unknown error'}"
            )
        manifest.append({**segment, "clip_path": str(clip_path)})

    manifest_path = destination / "segments.json"
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing manifest: {manifest_path}")
    manifest_path.write_text(
        json.dumps(
            {
                "audio_path": str(source),
                "audio_duration_sec": audio_duration_sec,
                "timestamp_format": manifest[0]["timestamp_format"],
                "timeline_clipped_to_audio_end": timeline_clipped_to_audio_end,
                "structure_anomalies": structure_anomalies,
                "segments": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "audio_path": str(source),
        "output_dir": str(destination),
        "manifest_path": str(manifest_path),
        "audio_duration_sec": audio_duration_sec,
        "timestamp_format": manifest[0]["timestamp_format"],
        "timeline_clipped_to_audio_end": timeline_clipped_to_audio_end,
        "structure_anomalies": structure_anomalies,
        "segment_count": len(manifest),
        "segments": manifest,
    }
