"""MCP tool: extract video frames with dynamic resolution and FPS.

Reusable ffmpeg helpers live in shared.video; this module is the read_video tool:
arg model, frame sampling, and response assembly.
"""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

from pydantic import BaseModel

from shared.content import image, text, text_error
from shared.env import (
    DEFAULT_BUDGET,
    DEFAULT_FPS,
    MAX_RESPONSE_BYTES,
    MAX_TOTAL_FRAMES,
    VIDEO_BUDGET_TOKENS,
    VIDEO_MIN_PIXELS,
)
from shared.image import budget_to_pixels, smart_resize
from shared.video import (
    compute_dynamic_fps,
    extract_frames_by_seeking,
    format_timestamp,
    get_video_info,
    parse_time,
)

MIN_FRAMES = 2
# Rough base64 bytes per pixel, used when sampling fails.
B64_BYTES_PER_PIXEL = 0.35


class ReadVideoArgs(BaseModel):
    video_path: str
    fps: float = 0
    max_frames: int = MAX_TOTAL_FRAMES
    budget: Literal["small", "normal", "large"] = "normal"
    start_time: Optional[float | str] = None
    end_time: Optional[float | str] = None


TOOL = {"name": "read_video", "args": ReadVideoArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract frames from a video file with dynamic resolution and FPS. When fps=0 (default),
    automatically selects the best sampling rate based on video duration. Resolution is
    automatically adjusted to fit the patch grid. For full source properties (codec, bitrate, native
    fps, rotation, VFR, audio tracks) — and before any clip/edit task — run media_info first.

    Args:
        video_path: Absolute path to the video file
        fps: Sampling FPS. 0 = auto-detect based on duration (recommended). Default: 0
        max_frames: Maximum frames to extract (capped at 600). Actual count may be lower or stripped
            depending on video length and response size limits.
        budget: Per-frame resolution preset: small (~288×288), normal (~512×512), large
            (~1024×1024).
        start_time: Start time — seconds or a clock string ('MM:SS'/'HH:MM:SS'). Default: 0
            (beginning).
        end_time: End time — seconds or a clock string ('MM:SS'/'HH:MM:SS'). Default: end of video.
    """
    video_path = arguments.get("video_path", "")
    if not os.path.isfile(video_path):
        return text_error(f"file not found: {video_path}")

    budget = arguments.get("budget", DEFAULT_BUDGET)
    max_pixels = budget_to_pixels(budget, VIDEO_BUDGET_TOKENS)
    min_pixels = VIDEO_MIN_PIXELS
    max_frames = min(arguments.get("max_frames", MAX_TOTAL_FRAMES), MAX_TOTAL_FRAMES)
    requested_fps = arguments.get("fps", 0)

    info = get_video_info(video_path)
    duration = info["duration"]
    if duration <= 0:
        return text_error("cannot determine video duration")

    start_time = parse_time(arguments.get("start_time", 0.0))
    end_time = parse_time(arguments.get("end_time"))

    if start_time is None or start_time < 0:
        start_time = 0.0
    if start_time >= duration:
        return text_error(f"start_time ({start_time}s) >= video duration ({duration:.1f}s)")
    if end_time is not None:
        if end_time <= start_time:
            return text_error(f"end_time ({end_time}s) must be greater than start_time ({start_time}s)")
        end_time = min(end_time, duration)

    segment_duration = (end_time if end_time is not None else duration) - start_time

    target_h, target_w = smart_resize(
        info["height"],
        info["width"],
        min_pixels,
        max_pixels,
    )

    # ── Stage 1: target count from requested or auto-selected FPS ─────
    if requested_fps > 0:
        fps = min(requested_fps, info["native_fps"])
        nframes = int(segment_duration * fps)
        nframes = max(MIN_FRAMES, min(max_frames, nframes))
    else:
        fps, nframes = compute_dynamic_fps(
            segment_duration,
            info["native_fps"],
            MIN_FRAMES,
            max_frames,
            DEFAULT_FPS,
        )

    # Stage 2: sample one mid-video frame to measure per-frame size; pre-cap count.
    sample = extract_frames_by_seeking(video_path, [start_time + segment_duration * 0.5], target_h, target_w)
    bytes_per_frame = len(sample[0][1]) if sample else int(target_h * target_w * B64_BYTES_PER_PIXEL)
    max_safe_frames = max(MIN_FRAMES, MAX_RESPONSE_BYTES // bytes_per_frame)
    nframes = min(nframes, max_safe_frames)

    # Stage 3: extract frames; uniformly downsample if total exceeds MAX_RESPONSE_BYTES.
    seg_end = end_time if end_time is not None else duration
    seek_end = min(seg_end, duration - 1.0 / info["native_fps"])
    span = seek_end - start_time
    if nframes <= 1 or span <= 0:
        timestamps = [start_time]
    else:
        step = span / (nframes - 1)
        timestamps = [start_time + i * step for i in range(nframes)]
    frames = extract_frames_by_seeking(video_path, timestamps, target_h, target_w)

    total_size = sum(len(b64) for _, b64 in frames)
    while total_size > MAX_RESPONSE_BYTES and len(frames) > MIN_FRAMES:
        keep = max(MIN_FRAMES, int(len(frames) * MAX_RESPONSE_BYTES / total_size))
        step = (len(frames) - 1) / (keep - 1) if keep > 1 else 0
        frames = [frames[round(i * step)] for i in range(keep)]
        total_size = sum(len(b64) for _, b64 in frames)

    fps = len(frames) / segment_duration if segment_duration > 0 else fps

    first_ts = frames[0][0] if frames else start_time
    last_ts = frames[-1][0] if frames else start_time
    rotation = info.get("rotation") or 0
    rotation_note = f" | rotation {rotation}°" if rotation else ""
    time_range = f"{format_timestamp(first_ts, last_ts)}–{format_timestamp(last_ts, last_ts)}"
    summary = (
        f"Video: {video_path}\n"
        f"Source: {duration:.1f}s | {info['width']}x{info['height']} (WxH) | "
        f"{info['native_fps']:.1f} fps native{rotation_note} "
        f"— call the media_info tool for codecs, bitrate, audio tracks, VFR\n"
        f"Sampled: {len(frames)} frames @ {fps:.1f} fps | {time_range} | {target_w}x{target_h} (WxH) per frame"
    )

    content: list[dict[str, Any]] = [text(summary)]
    for ts, b64 in frames:
        ts_display = format_timestamp(ts, last_ts)
        content.append(text(f"<{ts_display}>"))
        content.append(image(b64))
    return content
