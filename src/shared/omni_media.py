"""Shared local-media delivery for Qwen-Omni calls.

This module owns the file-management policy used by every Omni capability:

* preserve small audio files, otherwise extract/fit their audio track;
* upload over-limit local media through DashScope temporary OSS when available;
* fit local video into the endpoint's inline base64 budget;
* upload an oversized fitted video to OSS when configured;
* otherwise send sampled frames plus a fitted continuous audio track;
* optionally operate on a bounded time range without first materializing a large source clip.

Callers own the returned ``cleanup`` paths and must remove them after the model request completes.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from collections.abc import Callable

from shared import oss
from shared.api_omni import (
    OMNI_MAX_B64_BYTES,
    OMNI_MAX_B64_FRAMES,
    OMNI_MAX_UPLOAD_BYTES,
    OMNI_MIN_FRAMES,
    b64_len,
    has_video_stream,
    is_omni_url,
    jpeg_data_url,
    omni_audio_part,
    omni_frames_part,
    omni_video_part,
)
from shared.env import get_env
from shared.syscmd import find_tool

log = logging.getLogger(__name__)

ENCODE_ATTEMPTS = 3
VIDEO_AUDIO_KBPS = 32
MIN_VIDEO_KBPS = 64
SIZE_AIM = 0.9
VIDEO_CRF = 25
WAV_BYTES_PER_SEC = 16000 * 2
MP3_KBPS = (16, 24, 32, 40, 48, 64)
INLINE_B64_BUDGET = int(OMNI_MAX_B64_BYTES * 0.97)
FRAME_QUALITY = 4
B64_BYTES_PER_PIXEL = 0.25
MIN_FRAME_PIXELS = 160 * 160


class InlineBudgetExceeded(RuntimeError):
    """The media cannot fit inline and the caller should switch delivery strategy."""


def temp_file(suffix: str, *, prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


def cleanup_files(paths: list[str]) -> None:
    """Best-effort removal of temporary media created for one request."""
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def media_duration(path: str) -> float:
    """Container duration in seconds, or 0 when it cannot be determined."""
    from shared.video import probe_media

    try:
        return float(probe_media(path).get("format", {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001 — callers treat an unreadable duration as unknown
        return 0.0


def has_audio_stream(path: str) -> bool:
    """Whether a local media file carries an audio stream."""
    from shared.video import probe_media

    try:
        return any(stream.get("codec_type") == "audio" for stream in probe_media(path).get("streams", []))
    except Exception:  # noqa: BLE001 — an unprobeable source has no extractable track
        return False


def _range_args(start_time: float, duration: float | None) -> list[str]:
    args: list[str] = []
    if start_time > 0:
        args += ["-ss", f"{start_time:.3f}"]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    return args


def _audio_timeline_filter(duration: float | None) -> str:
    """Keep an extracted audio stream aligned to the selected media timeline."""
    filters = ["aresample=async=1:first_pts=0"]
    if duration is not None:
        filters.append("apad")
    return ",".join(filters)


def normalize_local_range(
    file_path: str,
    start_time: float,
    duration: float | None,
) -> tuple[float, float | None]:
    """Validate a local range and clamp it to the source duration when that is known."""
    if start_time < 0:
        raise ValueError("start_time must be non-negative")
    if duration is not None and duration <= 0:
        raise ValueError("duration must be greater than zero")
    if start_time == 0 and duration is None:
        return start_time, duration

    total_duration = media_duration(file_path)
    if total_duration <= 0:
        return start_time, duration
    if start_time >= total_duration:
        raise ValueError(f"start_time ({start_time:g}s) >= media duration ({total_duration:g}s)")

    remaining = total_duration - start_time
    return start_time, remaining if duration is None else min(duration, remaining)


def extract_audio(
    file_path: str,
    out_path: str,
    *,
    start_time: float = 0.0,
    duration: float | None = None,
) -> None:
    """Extract a whole track or time range as 16 kHz mono PCM."""
    cmd = [find_tool("ffmpeg"), "-v", "error", "-y"]
    if start_time > 0:
        cmd += ["-ss", f"{start_time:.3f}"]
    cmd += ["-i", file_path, *_range_args(0.0, duration)]
    cmd += [
        "-vn",
        "-af",
        _audio_timeline_filter(duration),
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        stderr = proc.stderr.decode().strip() if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg audio extraction failed: {stderr}")


def encode_audio(
    file_path: str,
    out_path: str,
    *,
    kbps: int,
    start_time: float = 0.0,
    duration: float | None = None,
) -> None:
    """Encode a whole audio track or time range as 16 kHz mono MP3."""
    cmd = [find_tool("ffmpeg"), "-v", "error", "-y"]
    if start_time > 0:
        cmd += ["-ss", f"{start_time:.3f}"]
    cmd += ["-i", file_path, *_range_args(0.0, duration)]
    cmd += [
        "-vn",
        "-af",
        _audio_timeline_filter(duration),
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{kbps}k",
        "-ar",
        "16000",
        "-ac",
        "1",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        stderr = proc.stderr.decode().strip() if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg audio encode failed: {stderr}")


def _audio_too_long_error(duration: float, max_bytes: int) -> InlineBudgetExceeded:
    fits_min = max_bytes * 8 / (MP3_KBPS[0] * 1000) / 60
    return InlineBudgetExceeded(
        f"the audio track is too long to upload inline: {duration:.0f}s cannot be encoded at ≥ "
        f"{MP3_KBPS[0]} kbps within the {max_bytes / 1e6:.1f} MB budget (the endpoint caps an inline "
        f"media item at {OMNI_MAX_B64_BYTES / 1e6:.0f} MB of base64), which tops out around "
        f"{fits_min:.0f} min. Trim it, pass an http(s)/OSS URL, or configure OSS_* for automatic upload."
    )


def fit_audio(
    file_path: str,
    out_path: str,
    *,
    budget: int,
    duration: float,
    start_time: float = 0.0,
    encode_audio_fn: Callable[..., None] = encode_audio,
) -> None:
    """Fit a whole audio track or time range into a raw-byte budget."""
    affordable = [rate for rate in MP3_KBPS if duration <= 0 or rate * 1000 / 8 * duration <= budget]
    if not affordable:
        raise _audio_too_long_error(duration, budget)
    for kbps in reversed(affordable):
        encode_audio_fn(file_path, out_path, kbps=kbps, start_time=start_time, duration=duration or None)
        size = os.path.getsize(out_path)
        if size <= budget:
            return
        log.info("audio at %d kbps is %.1f MB (cap %.1f MB); stepping down", kbps, size / 1e6, budget / 1e6)
    raise _audio_too_long_error(duration or media_duration(out_path), budget)


def local_audio_part(
    file_path: str,
    cleanup: list[str],
    *,
    budget: int = OMNI_MAX_UPLOAD_BYTES,
    start_time: float = 0.0,
    duration: float | None = None,
) -> dict:
    """Build an inline audio part, extracting and fitting only when needed."""
    effective_duration = duration if duration is not None else media_duration(file_path)
    is_range = start_time > 0 or duration is not None
    if not is_range and not has_video_stream(file_path) and os.path.getsize(file_path) <= budget:
        return omni_audio_part(file_path)
    if 0 < effective_duration and effective_duration * WAV_BYTES_PER_SEC <= budget:
        wav = temp_file(".wav", prefix="omni_asr_")
        cleanup.append(wav)
        extract_audio(file_path, wav, start_time=start_time, duration=effective_duration or None)
        return omni_audio_part(wav)
    mp3 = temp_file(".mp3", prefix="omni_asr_")
    cleanup.append(mp3)
    fit_audio(file_path, mp3, budget=budget, duration=effective_duration, start_time=start_time)
    return omni_audio_part(mp3)


def _video_kbps(duration: float, byte_budget: float) -> float:
    return byte_budget * 8 / duration / 1000 - VIDEO_AUDIO_KBPS


def _video_too_long_error(duration: float, max_bytes: int) -> InlineBudgetExceeded:
    fits_min = max_bytes * 8 / ((MIN_VIDEO_KBPS + VIDEO_AUDIO_KBPS) * 1000) / 60
    return InlineBudgetExceeded(
        f"video is too long to upload inline: {duration:.0f}s cannot be encoded at ≥ "
        f"{MIN_VIDEO_KBPS} kbps within the {max_bytes / 1e6:.1f} MB budget (the endpoint caps an "
        f"inline media item at {OMNI_MAX_B64_BYTES / 1e6:.0f} MB of base64), which tops out around "
        f"{fits_min:.1f} min at this fps"
    )


def encode_video(
    file_path: str,
    out_path: str,
    *,
    height: int,
    width: int,
    fps: float | None,
    video_kbps: float | None,
    start_time: float = 0.0,
    duration: float | None = None,
) -> None:
    """Transcode a whole video or time range for Omni sampling."""
    cmd = [find_tool("ffmpeg"), "-v", "error", "-y"]
    if start_time > 0:
        cmd += ["-ss", f"{start_time:.3f}"]
    cmd += ["-i", file_path, *_range_args(0.0, duration)]
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale={width}:{height}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(VIDEO_CRF),
    ]
    if fps:
        cmd += ["-r", f"{fps:g}"]
    if video_kbps:
        kbps = max(1, int(video_kbps))
        cmd += ["-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"]
    cmd += [
        "-c:a",
        "aac",
        "-b:a",
        f"{VIDEO_AUDIO_KBPS}k",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=900)
    if proc.returncode != 0:
        stderr = proc.stderr.decode().strip() if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg video preprocess failed: {stderr}")


def preprocess_video(
    file_path: str,
    out_path: str,
    max_pixels: int,
    *,
    fps: float | None = None,
    max_bytes: int = OMNI_MAX_UPLOAD_BYTES,
    start_time: float = 0.0,
    duration: float | None = None,
) -> None:
    """Fit a whole video or bounded time range into the inline upload budget."""
    from shared.env import TOKEN_SIZE
    from shared.image import smart_resize
    from shared.video import get_video_info

    info = get_video_info(file_path)
    height, width = smart_resize(info["height"], info["width"], TOKEN_SIZE * TOKEN_SIZE, max_pixels)
    effective_duration = duration if duration is not None else float(info.get("duration") or 0.0)
    rate = min(fps, info["native_fps"]) if fps and info.get("native_fps") else fps
    budget = max_bytes * SIZE_AIM
    video_kbps = _video_kbps(effective_duration, budget) if effective_duration > 0 else None
    if video_kbps is not None and video_kbps < MIN_VIDEO_KBPS:
        raise _video_too_long_error(effective_duration, max_bytes)

    for attempt in range(1, ENCODE_ATTEMPTS + 1):
        encode_video(
            file_path,
            out_path,
            height=height,
            width=width,
            fps=rate,
            video_kbps=video_kbps,
            start_time=start_time,
            duration=duration,
        )
        size = os.path.getsize(out_path)
        if size <= max_bytes:
            return
        if effective_duration <= 0:
            effective_duration = float(get_video_info(out_path).get("duration") or 0.0)
        if effective_duration <= 0:
            raise RuntimeError(
                f"transcoded video is {size / 1e6:.1f} MB, over the {max_bytes / 1e6:.1f} MB upload "
                "budget, and its duration could not be determined to retarget the bitrate"
            )
        video_kbps = (size * 8 / effective_duration / 1000) * (budget / size) - VIDEO_AUDIO_KBPS
        if video_kbps < MIN_VIDEO_KBPS:
            raise _video_too_long_error(effective_duration, max_bytes)
        log.info(
            "transcode attempt %d produced %.1f MB (cap %.1f MB); retrying at %.0f kbps",
            attempt,
            size / 1e6,
            max_bytes / 1e6,
            video_kbps,
        )
    raise InlineBudgetExceeded(
        f"could not transcode the video under the {max_bytes / 1e6:.1f} MB upload budget after "
        f"{ENCODE_ATTEMPTS} attempts"
    )


def transcode_and_upload(
    file_path: str,
    out_path: str,
    max_pixels: int,
    fps: float,
    *,
    start_time: float = 0.0,
    duration: float | None = None,
) -> str:
    """Transcode a whole video or time range without a byte cap, then upload it to OSS."""
    from shared.env import TOKEN_SIZE
    from shared.image import smart_resize
    from shared.video import get_video_info

    info = get_video_info(file_path)
    height, width = smart_resize(info["height"], info["width"], TOKEN_SIZE * TOKEN_SIZE, max_pixels)
    rate = min(fps, info["native_fps"]) if fps and info.get("native_fps") else fps
    encode_video(
        file_path,
        out_path,
        height=height,
        width=width,
        fps=rate,
        video_kbps=None,
        start_time=start_time,
        duration=duration,
    )
    return oss.upload_and_sign(out_path, key_prefix=get_env("OSS_VIDEO_CLIP_PREFIX", "tmp/video_clips"))


def fit_frames(
    file_path: str,
    duration: float,
    fps: float,
    max_pixels: int,
    budget: int,
    *,
    start_time: float = 0.0,
) -> tuple[list[str], list[float]]:
    """Sample a video range into JPEG data URLs that each fit ``budget`` encoded bytes."""
    from shared.env import TOKEN_SIZE
    from shared.image import smart_resize
    from shared.video import extract_frames_by_seeking, get_video_info

    info = get_video_info(file_path)
    count = min(OMNI_MAX_B64_FRAMES, max(OMNI_MIN_FRAMES, int(duration * fps)))
    pixels = min(max_pixels, max(MIN_FRAME_PIXELS, int(budget / B64_BYTES_PER_PIXEL)))
    height, width = smart_resize(info["height"], info["width"], TOKEN_SIZE * TOKEN_SIZE, pixels)
    relative_stamps = [round(index * duration / count, 2) for index in range(count)]
    absolute_stamps = [start_time + stamp for stamp in relative_stamps]
    frames = extract_frames_by_seeking(file_path, absolute_stamps, height, width, quality=FRAME_QUALITY)
    if len(frames) < OMNI_MIN_FRAMES:
        raise RuntimeError(f"could not extract enough frames from {os.path.basename(file_path)} to stand in for it")
    relative_frames = [(max(0.0, stamp - start_time), encoded) for stamp, encoded in frames]
    relative_frames.sort(key=lambda frame: frame[0])
    oversized = [len(encoded) for _, encoded in relative_frames if len(encoded) > budget]
    if oversized:
        raise InlineBudgetExceeded(
            f"a sampled frame needs {max(oversized) / 1e6:.2f} MB of base64, more than the per-image "
            f"{budget / 1e6:.2f} MB budget; lower max_pixels or pass an http(s)/OSS URL"
        )
    log.info("split video into %d frames at %d×%d", len(relative_frames), width, height)
    return [jpeg_data_url(encoded) for _, encoded in relative_frames], [stamp for stamp, _ in relative_frames]


def frames_note(stamps: list[float], duration: float, with_audio: bool) -> dict:
    """Give an image-list video a relative time base and explain its audio pairing."""
    listing = ", ".join(f"#{index + 1}={stamp:g}s" for index, stamp in enumerate(stamps))
    audio = (
        " The audio content part is the COMPLETE, uninterrupted sound track of the same interval — use it, "
        "not the frames, for anything spoken or heard, and trust its continuous timeline."
        if with_audio
        else " This video interval has no audio track."
    )
    return {
        "type": "text",
        "text": (
            f"The supplied video interval is {duration:.0f}s long and too large to upload whole, so it is "
            f"provided as {len(stamps)} frames sampled uniformly in chronological order. Each frame's "
            f"timestamp relative to this interval: {listing}. Express every time reference on this relative "
            f"timeline — do NOT assume the frames are one second apart.{audio}"
        ),
    }


def frames_and_audio_parts(
    file_path: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    *,
    start_time: float = 0.0,
    duration: float | None = None,
    inline_b64_budget: int = INLINE_B64_BUDGET,
    fit_audio_fn: Callable[..., None] = fit_audio,
    fit_frames_fn: Callable[..., tuple[list[str], list[float]]] = fit_frames,
) -> list[dict]:
    """Represent an oversized video range as frames, optional audio, and a timestamp note.

    ``inline_b64_budget`` is a per-image encoded budget. Audio has its own independent per-item raw
    upload budget, matching the endpoint's per-media-item 10 MB limit.
    """
    effective_duration = duration if duration is not None else media_duration(file_path)
    if effective_duration <= 0:
        raise RuntimeError("cannot split a video of unknown duration into frames + audio")

    audio_part = None
    if has_audio_stream(file_path):
        mp3 = temp_file(".mp3", prefix="omni_av_")
        cleanup.append(mp3)
        fit_audio_fn(
            file_path,
            mp3,
            budget=OMNI_MAX_UPLOAD_BYTES,
            duration=effective_duration,
            start_time=start_time,
        )
        audio_part = omni_audio_part(mp3)

    # DashScope caps the total number of data-URI items in one request at 250. The frame-list
    # limit alone is also 250, so reserve one slot when the continuous audio track is inline.
    frame_fps = fps
    if audio_part is not None:
        frame_fps = min(fps, (OMNI_MAX_B64_FRAMES - 1) / effective_duration)

    urls, stamps = fit_frames_fn(
        file_path,
        effective_duration,
        frame_fps,
        max_pixels,
        inline_b64_budget,
        start_time=start_time,
    )
    parts = [omni_frames_part(urls)]
    if audio_part:
        parts.append(audio_part)
    parts.append(frames_note(stamps, effective_duration, audio_part is not None))
    return parts


def local_video_parts(
    file_path: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    max_video_sec: float | None,
    model: str,
    *,
    start_time: float = 0.0,
    duration: float | None = None,
    max_upload_bytes: int = OMNI_MAX_UPLOAD_BYTES,
    preprocess_video_fn: Callable[..., None] = preprocess_video,
    frames_and_audio_parts_fn: Callable[..., list[dict]] = frames_and_audio_parts,
    transcode_and_upload_fn: Callable[..., str] = transcode_and_upload,
) -> list[dict]:
    """Route a local video range: inline file, OSS URL, or frames plus audio."""
    from shared.video import video_duration_exceeds

    exceeds = duration > max_video_sec if duration is not None and max_video_sec else False
    if duration is None:
        exceeds = video_duration_exceeds(file_path, max_video_sec)
    if exceeds:
        log.warning(
            "video interval from %s exceeds the server-side duration limit for model %r; sending frames + audio",
            file_path,
            model,
        )
        return frames_and_audio_parts_fn(
            file_path,
            fps,
            max_pixels,
            cleanup,
            start_time=start_time,
            duration=duration,
        )

    mp4 = temp_file(".mp4", prefix="omni_av_")
    cleanup.append(mp4)
    try:
        preprocess_video_fn(
            file_path,
            mp4,
            max_pixels,
            fps=fps,
            max_bytes=max_upload_bytes,
            start_time=start_time,
            duration=duration,
        )
    except InlineBudgetExceeded as error:
        log.info("%s; switching delivery", error)
        if oss.is_upload_configured():
            try:
                url = transcode_and_upload_fn(
                    file_path,
                    mp4,
                    max_pixels,
                    fps,
                    start_time=start_time,
                    duration=duration,
                )
            except Exception as upload_error:  # noqa: BLE001 — frames remain a local fallback
                log.warning("OSS upload failed (%s); falling back to frames + audio", upload_error)
            else:
                return [omni_video_part(url, fps=fps, max_pixels=max_pixels)]
        return frames_and_audio_parts_fn(
            file_path,
            fps,
            max_pixels,
            cleanup,
            start_time=start_time,
            duration=duration,
        )
    except Exception as error:  # noqa: BLE001 — preserve the existing small-file fallback
        log.warning("video preprocess failed (%s)", error)
        if start_time > 0 or duration is not None or os.path.getsize(file_path) > max_upload_bytes:
            raise
        log.warning("sending the original file instead")
        return [omni_video_part(file_path, fps=fps, max_pixels=max_pixels)]
    return [omni_video_part(mp4, fps=fps, max_pixels=max_pixels)]


def temporary_oss_parts(
    file_path: str,
    mode: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    max_video_sec: float | None,
    model: str,
    base_url: str,
    api_key: str,
    *,
    max_b64_bytes: int = OMNI_MAX_B64_BYTES,
    encode_audio_fn: Callable[..., None] = encode_audio,
) -> list[dict] | None:
    """Upload an over-inline-limit local input through DashScope's temporary OSS service."""
    if b64_len(os.path.getsize(file_path)) <= max_b64_bytes:
        return None

    from shared import dashscope_upload

    if not dashscope_upload.is_available(base_url, api_key):
        return None

    video = has_video_stream(file_path)
    if video and mode != "audio":
        from shared.video import video_duration_exceeds

        if video_duration_exceeds(file_path, max_video_sec):
            return None
    try:
        upload_path = file_path
        send_as_video = video
        if video and mode == "audio":
            upload_path = temp_file(".mp3", prefix="omni_asr_")
            cleanup.append(upload_path)
            encode_audio_fn(file_path, upload_path, kbps=MP3_KBPS[-1])
            send_as_video = False
        url = dashscope_upload.upload_temporary_file(
            upload_path,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    except Exception as upload_error:  # noqa: BLE001 — preserve the local fallback chain
        log.warning("DashScope temporary OSS upload failed (%s); falling back to local delivery", upload_error)
        return None
    if send_as_video:
        return [omni_video_part(url, fps=fps, max_pixels=max_pixels)]
    return [omni_audio_part(url, audio_format=os.path.splitext(upload_path)[1].lstrip("."))]


def build_media_parts(
    file_path: str,
    mode: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    max_video_sec: float | None,
    model: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    start_time: float = 0.0,
    duration: float | None = None,
    max_upload_bytes: int = OMNI_MAX_UPLOAD_BYTES,
) -> list[dict]:
    """Build Omni content parts using the shared local-file delivery policy."""
    if is_omni_url(file_path):
        if start_time != 0 or duration is not None:
            raise ValueError("time-range perception requires a local media file")
        return (
            [omni_video_part(file_path, fps=fps, max_pixels=max_pixels)]
            if mode == "video" or (mode != "audio" and has_video_stream(file_path))
            else [omni_audio_part(file_path)]
        )
    start_time, duration = normalize_local_range(file_path, start_time, duration)
    if base_url is not None and api_key is not None and start_time == 0 and duration is None:
        temporary = temporary_oss_parts(
            file_path,
            mode,
            fps,
            max_pixels,
            cleanup,
            max_video_sec,
            model,
            base_url,
            api_key,
        )
        if temporary is not None:
            return temporary
    if mode == "audio" or not has_video_stream(file_path):
        return [
            local_audio_part(
                file_path,
                cleanup,
                budget=max_upload_bytes,
                start_time=start_time,
                duration=duration,
            )
        ]
    return local_video_parts(
        file_path,
        fps,
        max_pixels,
        cleanup,
        max_video_sec,
        model,
        start_time=start_time,
        duration=duration,
        max_upload_bytes=max_upload_bytes,
    )
