"""Video processing helpers shared across capabilities: ffprobe info, dynamic FPS, timestamp
parsing, and parallel keyframe-seek frame extraction. Part of the `shared` library — depends only
on ffmpeg/ffprobe (located via shared.syscmd.find_tool) and the stdlib.
"""

from __future__ import annotations

import base64
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

from shared.env import FFMPEG_TIMEOUT
from shared.syscmd import find_tool

SEEK_MAX_WORKERS = 16


def parse_time(value: float | int | str | None) -> float | None:
    """Parse a timestamp to seconds (None passes through, for optional inputs).

    Accepts a number of seconds, or a clock string 'SS', 'MM:SS', or 'HH:MM:SS' (fractional
    seconds allowed) — the same form read_video prints, so a displayed timestamp can be pasted back.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s.endswith("s"):  # tolerate the trailing-'s' form read_video prints (<12.3s>)
        s = s[:-1].strip()
    if ":" not in s:
        return float(s)
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError(f"invalid timestamp: {value!r}")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def format_timestamp(seconds: float, max_seconds: float) -> str:
    if max_seconds >= 3600:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:04.1f}"
    elif max_seconds >= 60:
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m}:{s:04.1f}"
    else:
        return f"{seconds:.1f}s"


def probe_media(path: str) -> dict:
    """Full ffprobe of a media file (video or audio): container/format info, every stream
    (video/audio/subtitle/data), and chapters. Returns the parsed ffprobe JSON:
    ``{"format": {...}, "streams": [...], "chapters": [...]}``.
    """
    result = subprocess.run(
        [
            find_tool("ffprobe"),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip() or 'unknown error'}")
    return json.loads(result.stdout)


def video_duration_exceeds(path: str, max_seconds: float | None) -> bool:
    """True when a LOCAL file's duration exceeds ``max_seconds`` — the signal to prefer local frame
    sampling over handing the whole file to the endpoint for server-side sampling (which caps video
    duration per model).

    False for a remote URL (can't probe; the server enforces its own limit), a falsy ``max_seconds``
    (unknown model → no cap to apply), or an unprobeable file (ffprobe missing/unreadable) — the caller
    should not force a downgrade on uncertainty.
    """
    if not max_seconds or path.startswith(("http://", "https://", "data:")):
        return False
    try:
        duration = float(probe_media(path).get("format", {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001 — ffprobe missing/unreadable: don't downgrade on uncertainty
        return False
    return duration > max_seconds


def get_video_info(video_path: str) -> dict:
    """Probe a video for the numbers frame extraction needs: display ``width``/``height``, duration,
    native fps, and the ``rotation`` flag. Dimensions are the *display* orientation (stored pair
    swapped on a quarter turn) — ffmpeg autorotates on decode, so that is what a ``-vf`` filter and
    the extracted frames actually see. Use ``probe_media`` / ``media_info`` for the stored geometry.
    """
    result = subprocess.run(
        [
            find_tool("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,r_frame_rate,nb_frames:stream_tags=rotate:stream_side_data=rotation",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            video_path,
        ],
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip() or 'unknown error'}")
    probe = json.loads(result.stdout)
    stream = probe.get("streams", [{}])[0]

    width = int(stream.get("width", 0))
    height = int(stream.get("height", 0))

    duration = float(stream.get("duration", 0))
    if duration <= 0:
        duration = float(probe.get("format", {}).get("duration", 0))

    native_fps = 30.0
    r_frame_rate = stream.get("r_frame_rate", "")
    if r_frame_rate and "/" in r_frame_rate:
        num, den = r_frame_rate.split("/")
        # Guard against "0/0" and "0/1" (both seen in the wild) — a 0 fps here would divide by zero
        # downstream in read_video; keep the 30.0 fallback in that case.
        if int(den) > 0 and int(num) > 0:
            native_fps = int(num) / int(den)

    # Display rotation (phones/action cams store sideways pixels + a rotate flag): prefer the
    # Display-Matrix side-data value, fall back to the legacy `rotate` tag. 0 when absent.
    rotation = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                rotation = 0
            break
    else:
        rotate_tag = (stream.get("tags") or {}).get("rotate")
        if rotate_tag:
            try:
                rotation = int(float(rotate_tag))
            except (TypeError, ValueError):
                rotation = 0

    # ffmpeg autorotates on decode (default since 2.7), so a quarter-turn clip is already upright when
    # a caller's `-vf scale=w:h` runs. Report the display pair: scaling an upright frame to the stored
    # (sideways) target is what distorts portrait phone video into a stretched landscape frame.
    if abs(rotation) % 180 == 90:
        width, height = height, width

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "native_fps": native_fps,
        "rotation": rotation,
    }


def compute_dynamic_fps(
    duration: float, native_fps: float, min_frames: int, max_frames: int, default_fps: float
) -> tuple[float, int]:
    nframes = int(duration * default_fps)
    nframes = max(min_frames, min(max_frames, nframes))

    fps = nframes / duration if duration > 0 else default_fps
    fps = min(fps, native_fps)
    return fps, nframes


def extract_frames_by_seeking(
    video_path: str,
    timestamps: list[float],
    target_h: int,
    target_w: int,
    max_workers: int = SEEK_MAX_WORKERS,
    quality: int = 2,
) -> list[tuple[float, str]]:
    """Extract frames via parallel keyframe-seeking. Much faster for sparse sampling.

    ``quality`` is ffmpeg's mjpeg ``-q:v`` (2 = best, 31 = worst) — raise it when the frames have to
    fit a byte budget rather than look their best.
    """

    vf = f"scale={target_w}:{target_h}" if target_w > 0 and target_h > 0 else None
    ffmpeg = find_tool("ffmpeg")  # resolve once, not per-frame

    def _extract_one(ts: float) -> tuple[float, str] | None:
        cmd = [ffmpeg, "-nostdin", "-v", "error", "-ss", str(ts), "-i", video_path, "-an", "-frames:v", "1"]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", str(quality), "pipe:1"]
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
        if proc.returncode == 0 and proc.stdout:
            return (round(ts, 1), base64.b64encode(proc.stdout).decode("ascii"))
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_extract_one, timestamps))
    return [r for r in results if r is not None]
