"""MCP tool: detect scene cuts in a video via ffmpeg's scene filter."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import (
    _PTS_TIME_RE,
    MediaOpsError,
    _run,
    ffmpeg_path,
    get_video_metadata,
    validate_video_path,
)


class DetectScenesArgs(BaseModel):
    video_path: str = Field()
    threshold: float = Field(default=0.3)
    min_scene_sec: float = Field(default=1.0)
    start: float | None = Field(default=None)
    end: float | None = Field(default=None)


TOOL: dict[str, Any] = {"name": "detect_scenes", "args": DetectScenesArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Detect hard cuts in a video using ffmpeg's scene filter (no LLM). Returns contiguous scene intervals
    with timestamps.

    Args:
        video_path: Path to the source video file.
        threshold: Scene change threshold (0.1–0.9, lower = more sensitive).
        min_scene_sec: Minimum scene duration in seconds (shorter scenes merge into predecessor).
        start: Optional start time to probe a sub-range.
        end: Optional end time to probe a sub-range.
    """
    raw_path = arguments["video_path"]
    threshold = arguments.get("threshold", 0.3)
    min_scene_sec = arguments.get("min_scene_sec", 1.0)
    start = arguments.get("start")
    end = arguments.get("end")

    path = validate_video_path(raw_path)
    meta = get_video_metadata(str(path))
    threshold = min(max(float(threshold), 0.1), 0.9)
    min_scene_sec = max(float(min_scene_sec), 0.2)
    duration = max(meta.duration_sec, 0.1)

    seg_start = min(max(0.0, float(start)), duration) if start is not None else 0.0
    seg_end = min(max(0.0, float(end)), duration) if end is not None else duration
    if seg_end <= seg_start:
        raise MediaOpsError("`end` must be greater than `start`.")

    proc = _run(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-ss",
            f"{seg_start:.3f}",
            "-to",
            f"{seg_end:.3f}",
            "-i",
            str(path),
            "-vf",
            f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=900,
    )

    span = seg_end - seg_start
    cuts = sorted(
        {round(seg_start + float(m), 3) for m in _PTS_TIME_RE.findall(proc.stderr or "") if 0.0 < float(m) < span}
    )

    boundaries = [round(seg_start, 3)]
    for cut in cuts:
        if cut - boundaries[-1] >= min_scene_sec:
            boundaries.append(cut)
    if seg_end - boundaries[-1] < min_scene_sec and len(boundaries) > 1:
        boundaries.pop()
    boundaries.append(round(seg_end, 3))

    scenes = [
        {
            "start": boundaries[i],
            "end": boundaries[i + 1],
            "duration": round(boundaries[i + 1] - boundaries[i], 3),
        }
        for i in range(len(boundaries) - 1)
    ]

    result = {
        "threshold": threshold,
        "range": [round(seg_start, 3), round(seg_end, 3)],
        "cut_count": len(cuts),
        "scene_count": len(scenes),
        "scenes": scenes,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
