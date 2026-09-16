"""Local video probing, sampling, frame extraction, and model-ready chunk preparation."""

from __future__ import annotations

import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from shared.env import FFMPEG_TIMEOUT, get_env
from shared.syscmd import find_tool
from shared.video import probe_media

from .schemas import ProbeResult

_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts", ".m2ts", ".mpg", ".mpeg"}
)
_URL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SCENE_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class ExtractedFrame:
    timestamp: float
    path: Path
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp, "path": str(self.path), "source": self.source}


@dataclass(frozen=True)
class MediaChunk:
    path: Path
    source_start: float
    source_end: float
    delivery: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "source_start": self.source_start,
            "source_end": self.source_end,
            "delivery": dict(self.delivery),
        }


def validate_local_video(path: str | Path) -> Path:
    raw = str(path)
    if _URL_PATTERN.match(raw):
        raise ValueError("only local video files are accepted")
    source = Path(raw).expanduser().resolve()
    if not source.is_file():
        raise ValueError("video must be an existing regular file")
    if source.suffix.lower() not in _VIDEO_SUFFIXES:
        raise ValueError(f"unsupported local video extension: {source.suffix or '<none>'}")
    return source


def _rate(value: str | int | float | None) -> float:
    if value in (None, "", "0/0"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    numerator, separator, denominator = value.partition("/")
    if separator:
        divisor = float(denominator)
        return float(numerator) / divisor if divisor else 0.0
    return float(value)


def probe_video(path: str | Path) -> ProbeResult:
    """Probe one local video and return normalized display/audio metadata."""
    source = validate_local_video(path)
    raw = probe_media(str(source))
    streams = raw.get("streams") or []
    video_stream = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video_stream is None:
        raise ValueError("input has no video stream")
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    rotation = 0
    for side_data in video_stream.get("side_data_list") or []:
        if "rotation" in side_data:
            rotation = int(float(side_data["rotation"]))
            break
    else:
        rotation = int(float((video_stream.get("tags") or {}).get("rotate") or 0))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    duration = float(video_stream.get("duration") or raw.get("format", {}).get("duration") or 0)
    fps = _rate(video_stream.get("avg_frame_rate")) or _rate(video_stream.get("r_frame_rate"))
    result = ProbeResult(
        path=str(source),
        size_bytes=source.stat().st_size,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        video_codec=str(video_stream.get("codec_name") or "unknown"),
        has_audio=audio_stream is not None,
        audio_codec=str(audio_stream.get("codec_name") or "") if audio_stream else "",
        format_name=str(raw.get("format", {}).get("format_name") or ""),
        rotation=rotation,
        video_stream_index=int(video_stream.get("index", 0)),
    )
    result.validate()
    return result


def _bounded_time(timestamp: float, duration: float) -> float:
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("timestamp must be finite and non-negative")
    if duration <= 0:
        raise ValueError("duration must be positive")
    safe_end = duration / 2 if duration <= 0.2 else duration - 0.1
    return min(timestamp, max(0.0, safe_end))


def extract_frame(
    video_path: str | Path,
    timestamp: float,
    output_path: str | Path,
    *,
    width: int | None = None,
    jpeg_quality: int = 2,
    _probe_result: ProbeResult | None = None,
) -> Path:
    """Extract one JPEG with keyframe seeking and atomic replacement."""
    source = validate_local_video(video_path)
    media_probe = _probe_result or probe_video(source)
    timestamp = _bounded_time(timestamp, media_probe.duration)
    # Container duration can extend beyond the final decodable video frame (commonly when audio is
    # slightly longer). Keep end samples at least one frame interval inside the stream.
    timestamp = min(timestamp, max(0.0, media_probe.duration - max(0.1, 1.0 / media_probe.fps)))
    if width is not None and width < 2:
        raise ValueError("frame width must be at least 2")
    if not 2 <= jpeg_quality <= 31:
        raise ValueError("jpeg_quality must be in [2, 31]")
    output = Path(output_path).expanduser().resolve()
    if output == source:
        raise ValueError("frame output cannot overwrite the input video")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid4().hex}.tmp{output.suffix or '.jpg'}")
    command = [
        find_tool("ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.6f}",
        "-i",
        str(source),
        "-map",
        f"0:{media_probe.video_stream_index}",
        "-an",
        "-frames:v",
        "1",
    ]
    if width is not None:
        command.extend(["-vf", f"scale={width}:-2"])
    command.extend(
        ["-pix_fmt", "yuvj420p", "-threads", "1", "-q:v", str(jpeg_quality), "-y", str(temporary)]
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr.strip() or 'empty output'}")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def extract_frames(
    video_path: str | Path,
    timestamps: list[float],
    output_dir: str | Path,
    *,
    prefix: str = "frame",
    width: int | None = None,
    jpeg_quality: int = 2,
    max_workers: int = 4,
) -> list[ExtractedFrame]:
    source = validate_local_video(video_path)
    media_probe = probe_video(source)
    safe_end = max(0.0, media_probe.duration - max(0.1, 1.0 / media_probe.fps))
    unique = sorted(
        {round(min(_bounded_time(float(value), media_probe.duration), safe_end), 3) for value in timestamps}
    )
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if max_workers < 1:
        raise ValueError("max_workers must be positive")

    def run(timestamp: float) -> ExtractedFrame:
        millis = round(timestamp * 1000)
        path = directory / f"{prefix}-{millis:012d}.jpg"
        extract_frame(
            source,
            timestamp,
            path,
            width=width,
            jpeg_quality=jpeg_quality,
            _probe_result=media_probe,
        )
        return ExtractedFrame(round(timestamp, 3), path, "timestamp")

    with ThreadPoolExecutor(max_workers=min(max_workers, len(unique) or 1)) as executor:
        return list(executor.map(run, unique))


def uniform_anchor_times(duration: float, count: int) -> list[float]:
    if not math.isfinite(duration) or duration <= 0 or count < 1:
        raise ValueError("duration and anchor count must be positive")
    if count == 1:
        return [round(duration / 2, 3)]
    end = max(0.0, duration - 0.001)
    return [round(index * end / (count - 1), 3) for index in range(count)]


def scene_change_times(
    video_path: str | Path,
    *,
    threshold: float = 0.25,
    limit: int = 48,
) -> list[float]:
    """Detect scene boundaries with ffmpeg's deterministic scene score filter."""
    source = validate_local_video(video_path)
    if not 0 <= threshold <= 1 or limit < 1:
        raise ValueError("invalid scene detection threshold or limit")
    filter_value = f"scale=320:-2,select='gt(scene,{threshold:.6f})',showinfo"
    command = [
        find_tool("ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(source),
        "-vf",
        filter_value,
        "-an",
        # ffmpeg 8 removed -vsync; -fps_mode is the replacement and has existed since ffmpeg 5.0.
        "-fps_mode",
        "vfr",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=max(FFMPEG_TIMEOUT, 300))
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg scene detection failed: {result.stderr.strip() or 'unknown error'}")
    values = sorted({round(float(match.group(1)), 3) for match in _SCENE_TIME.finditer(result.stderr)})
    if len(values) <= limit:
        return values
    if limit == 1:
        return [values[len(values) // 2]]
    return [values[round(index * (len(values) - 1) / (limit - 1))] for index in range(limit)]


def coarse_sample_times(
    video_path: str | Path,
    *,
    max_frames: int = 12,
    candidate_limit: int = 48,
    scene_threshold: float = 0.25,
) -> list[float]:
    """Blend uniformly spaced anchors with scene boundaries into a bounded rough sample."""
    source = validate_local_video(video_path)
    probe = probe_video(source)
    if max_frames < 2 or candidate_limit < max_frames:
        raise ValueError("coarse sampling needs max_frames >= 2 and candidate_limit >= max_frames")
    anchor_count = max(2, (max_frames + 1) // 2)
    anchors = uniform_anchor_times(probe.duration, anchor_count)
    scenes = scene_change_times(source, threshold=scene_threshold, limit=candidate_limit)
    scene_slots = max_frames - len(anchors)
    if len(scenes) > scene_slots > 0:
        if scene_slots == 1:
            scenes = [scenes[len(scenes) // 2]]
        else:
            scenes = [
                scenes[round(index * (len(scenes) - 1) / (scene_slots - 1))]
                for index in range(scene_slots)
            ]
    combined = sorted(set(anchors + scenes))
    if len(combined) < max_frames:
        combined = sorted(set(combined + uniform_anchor_times(probe.duration, max_frames)))
    if len(combined) > max_frames:
        combined = [combined[round(index * (len(combined) - 1) / (max_frames - 1))] for index in range(max_frames)]
    return combined


def extract_coarse_frames(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    max_frames: int = 12,
    candidate_limit: int = 48,
    scene_threshold: float = 0.25,
    width: int | None = 1280,
) -> list[ExtractedFrame]:
    times = coarse_sample_times(
        video_path,
        max_frames=max_frames,
        candidate_limit=candidate_limit,
        scene_threshold=scene_threshold,
    )
    frames = extract_frames(video_path, times, output_dir, prefix="coarse", width=width)
    return [ExtractedFrame(frame.timestamp, frame.path, "anchor_or_scene") for frame in frames]


def step_window(start: float, end: float, duration: float, padding: float = 2.0) -> tuple[float, float]:
    if not all(math.isfinite(value) for value in (start, end, duration, padding)):
        raise ValueError("step window values must be finite")
    if duration <= 0 or start < 0 or end < start or padding < 0:
        raise ValueError("invalid step window")
    return max(0.0, start - padding), min(duration, end + padding)


def sample_window_times(start: float, end: float, duration: float, step: float, limit: int) -> list[float]:
    start, end = step_window(start, end, duration, 0.0)
    if step <= 0 or limit < 1:
        raise ValueError("sampling step and limit must be positive")
    count = int(math.floor((end - start) / step)) + 1
    values = [round(start + index * step, 3) for index in range(count)]
    if not values or values[-1] < end - 1e-6:
        values.append(round(end, 3))
    if len(values) > limit:
        if limit == 1:
            return [values[len(values) // 2]]
        values = [values[round(index * (len(values) - 1) / (limit - 1))] for index in range(limit)]
    return list(dict.fromkeys(values))


def fine_sample_times(center: float, duration: float, radius: float, step: float, limit: int) -> list[float]:
    if radius <= 0:
        raise ValueError("fine sampling radius must be positive")
    return sample_window_times(max(0.0, center - radius), min(duration, center + radius), duration, step, limit)


@lru_cache(maxsize=1)
def _h264_encoder() -> str:
    result = subprocess.run(
        [find_tool("ffmpeg"), "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT,
    )
    listing = result.stdout + result.stderr
    for name in ("libx264", "libopenh264"):
        if re.search(rf"\b{re.escape(name)}\b", listing):
            return name
    raise RuntimeError("ffmpeg has no supported H.264 encoder (libx264 or libopenh264)")


def transcode_chunk(
    video_path: str | Path,
    output_path: str | Path,
    source_start: float,
    source_end: float,
    *,
    crf: int = 28,
    audio_bitrate: str = "96k",
    max_width: int = 1280,
    include_audio: bool = True,
    _probe_result: ProbeResult | None = None,
) -> Path:
    """Transcode a source span to H.264, optionally preserving the first audio stream."""
    source = validate_local_video(video_path)
    media_probe = _probe_result or probe_video(source)
    start, end = step_window(source_start, source_end, media_probe.duration, 0.0)
    if end <= start:
        raise ValueError("chunk source span must have positive duration")
    if not 0 <= crf <= 51 or max_width < 2:
        raise ValueError("invalid transcode quality settings")
    output = Path(output_path).expanduser().resolve()
    if output == source:
        raise ValueError("chunk output cannot overwrite the input video")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid4().hex}.tmp.mp4")
    scale = f"scale='min({max_width},iw)':-2"
    encoder = _h264_encoder()
    video_options = (
        ["-c:v", encoder, "-preset", "veryfast", "-crf", str(crf)]
        if encoder == "libx264"
        else ["-c:v", encoder, "-b:v", "600k" if crf >= 35 else "1500k"]
    )
    audio_options = (
        ["-map", "0:a:0?", "-c:a", "aac", "-b:a", audio_bitrate]
        if include_audio
        else ["-an"]
    )
    command = [
        find_tool("ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(source),
        "-t",
        f"{end - start:.6f}",
        "-map",
        f"0:{media_probe.video_stream_index}",
        *audio_options,
        "-vf",
        scale,
        *video_options,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-y",
        str(temporary),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=max(FFMPEG_TIMEOUT, 300))
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg chunk transcode failed: {result.stderr.strip() or 'empty output'}")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def prepare_video_chunks(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    chunk_seconds: float = 90.0,
    inline_budget_bytes: int | None = None,
    minimum_chunk_seconds: float = 4.0,
    allow_oss: bool = True,
    include_audio: bool = True,
) -> list[MediaChunk]:
    """Create model-ready chunks, recursively splitting oversized inline files.

    No model is called. Each result carries a local path, original source span, and delivery metadata
    containing either an inline local source or an optional signed OSS URL.
    """
    source = validate_local_video(video_path)
    probe = probe_video(source)
    if inline_budget_bytes is None:
        from shared.api_omni import OMNI_MAX_UPLOAD_BYTES

        inline_budget_bytes = OMNI_MAX_UPLOAD_BYTES
    if chunk_seconds <= 0 or minimum_chunk_seconds <= 0 or inline_budget_bytes < 1024:
        raise ValueError("invalid chunk duration or inline budget")
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    def build(start: float, end: float, *, compact: bool = False) -> list[MediaChunk]:
        start_ms, end_ms = round(start * 1000), round(end * 1000)
        suffix = "-compact" if compact else ""
        path = directory / f"chunk-{start_ms:012d}-{end_ms:012d}{suffix}.mp4"
        transcode_chunk(
            source,
            path,
            start,
            end,
            crf=35 if compact else 28,
            audio_bitrate="64k" if compact else "96k",
            max_width=960 if compact else 1280,
            include_audio=include_audio,
            _probe_result=probe,
        )
        size = path.stat().st_size
        if size <= inline_budget_bytes:
            return [
                MediaChunk(
                    path,
                    round(start, 3),
                    round(end, 3),
                    {"kind": "inline", "source": str(path), "bytes": size},
                )
            ]
        span = end - start
        if span > minimum_chunk_seconds * 1.5:
            path.unlink()
            midpoint = start + span / 2
            return build(start, midpoint) + build(midpoint, end)
        if not compact:
            path.unlink()
            return build(start, end, compact=True)
        if allow_oss:
            from shared import oss

            if oss.is_upload_configured():
                prefix = get_env("OSS_VIDEO_CLIP_PREFIX", "tmp/video_clips") or "tmp/video_clips"
                url = oss.upload_and_sign(str(path), key_prefix=prefix)
                return [
                    MediaChunk(
                        path,
                        round(start, 3),
                        round(end, 3),
                        {"kind": "oss", "source": url, "bytes": size},
                    )
                ]
        raise ValueError(
            f"chunk {start:.3f}-{end:.3f}s remains {size} bytes after recursive splitting and compact transcode"
        )

    chunks: list[MediaChunk] = []
    start = 0.0
    while start < probe.duration - 1e-6:
        end = min(probe.duration, start + chunk_seconds)
        chunks.extend(build(start, end))
        start = end
    return chunks


def chunks_to_json(chunks: list[MediaChunk]) -> str:
    return json.dumps([asdict(chunk) | {"path": str(chunk.path)} for chunk in chunks], ensure_ascii=False)
