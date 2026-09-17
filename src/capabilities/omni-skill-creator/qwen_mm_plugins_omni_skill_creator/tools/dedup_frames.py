"""MCP tool: perceptual-hash dedup over candidate video frames."""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import (
    MAX_DEDUP_CANDIDATES,
    _run_bytes,
    clamp_timestamps,
    ffmpeg_path,
    get_video_metadata,
    validate_video_path,
)


class DedupFramesArgs(BaseModel):
    video_path: str = Field()
    timestamps: list[float] | None = Field(default=None)
    interval: float | None = Field(default=None)
    hamming_threshold: int = Field(default=6)


TOOL: dict[str, Any] = {"name": "dedup_frames", "args": DedupFramesArgs}


def _dhash_bits(gray_9x8: bytes) -> int:
    bits = 0
    for row in range(8):
        for col in range(8):
            left = gray_9x8[row * 9 + col]
            right = gray_9x8[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _frame_dhash(path, ts: float) -> int | None:
    raw = _run_bytes(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ts:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=9:8",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    if len(raw) < 72:
        return None
    return _dhash_bits(raw[:72])


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Perceptual-hash dedup over candidate frames (no LLM, no image files). Collapses long static
    stretches so only distinct visuals need model attention.

    Args:
        video_path: Path to the source video file.
        timestamps: Candidate timestamps (seconds). If omitted, uses uniform sampling.
        interval: Uniform sampling interval in seconds (used when timestamps is omitted).
        hamming_threshold: Max Hamming distance to consider frames duplicates (0–32).
    """
    raw_path = arguments["video_path"]
    timestamps = arguments.get("timestamps")
    interval = arguments.get("interval")
    hamming_threshold = arguments.get("hamming_threshold", 6)

    path = validate_video_path(raw_path)
    meta = get_video_metadata(str(path))
    hamming_threshold = min(max(int(hamming_threshold), 0), 32)

    if timestamps:
        points = clamp_timestamps(
            timestamps,
            meta.duration_sec,
            MAX_DEDUP_CANDIDATES,
            hard_cap=MAX_DEDUP_CANDIDATES,
        )
    else:
        step = float(interval) if interval and interval > 0 else max(meta.duration_sec / 32.0, 1.0)
        count = min(max(1, math.floor(meta.duration_sec / step) + 1), MAX_DEDUP_CANDIDATES)
        points = clamp_timestamps(
            [round(idx * step, 3) for idx in range(count)],
            meta.duration_sec,
            MAX_DEDUP_CANDIDATES,
            hard_cap=MAX_DEDUP_CANDIDATES,
        )

    unique: list[dict] = []
    duplicates: list[dict] = []
    reps: list[tuple[float, int]] = []
    for ts in points:
        digest = _frame_dhash(path, ts)
        if digest is None:
            continue
        match = next(
            (rep_ts for rep_ts, rep_hash in reps if _hamming(digest, rep_hash) <= hamming_threshold),
            None,
        )
        if match is None:
            reps.append((ts, digest))
            unique.append({"seconds": ts, "dhash": f"{digest:016x}"})
        else:
            duplicates.append({"seconds": ts, "duplicate_of": match})

    result = {
        "hamming_threshold": hamming_threshold,
        "candidate_count": len(points),
        "unique": unique,
        "duplicates": duplicates,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
