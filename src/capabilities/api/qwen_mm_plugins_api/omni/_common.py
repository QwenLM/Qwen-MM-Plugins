"""Shared plumbing for the omni-av tools (not a tool itself — exports no TOOL/handle).

Every tool is the same shape: validate input → route the file to an audio or video content part →
either preview the request (``dry_run``) or call the Omni model and parse strict JSON. ``run_omni``
centralizes that so each tool module is just its prompt + args model + output formatting.

A local file can only be sent inline, as base64, and the endpoint caps that at 10 MB encoded (≈ 7 MB
of file — ``OMNI_MAX_UPLOAD_BYTES``), so local media is fitted to that budget first:

* audio (the ASR family): a file that already fits travels untouched; otherwise the track is pulled to
  16 kHz mono — lossless wav while that fits, else MP3 at the highest bitrate the budget affords.
* video (the AV tools): frames are smart_resize'd into ``max_pixels``, the frame rate is capped at the
  server's sampling ``fps``, and the result is bitrate-fitted under the budget.

A video too long to fit that way (roughly > 9 min at the default 1 fps / 448²) stops being squeezed
and switches strategy instead of shipping a request the endpoint would reject:

* with OSS configured — the transcode is uploaded and passed by signed URL, which has no size cap at
  all (Qwen3.5-Omni fetches up to 2 GB / 1 h server-side);
* without OSS — the video is decomposed into a uniformly sampled frame list plus its (fitted) audio
  track, which Omni combines into one clip; a note states each frame's timestamp, since the frame-list
  form carries no time base of its own.

If ffmpeg/ffprobe is unavailable or the transcode fails outright, the original file is sent unchanged
(the server then does its own sampling) — but only when the original already fits the budget.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from typing import Any

from shared import oss
from shared.api_omni import (
    _VIDEO_EXTS,
    DEFAULT_OMNI_FPS,
    DEFAULT_OMNI_MAX_PIXELS,
    OMNI_MAX_B64_BYTES,
    OMNI_MAX_B64_FRAMES,
    OMNI_MAX_UPLOAD_BYTES,
    OMNI_MIN_FRAMES,
    b64_len,
    call_omni,
    call_omni_json,
    has_video_stream,
    jpeg_data_url,
    omni_audio_part,
    omni_frames_part,
    omni_video_max_sec,
    omni_video_part,
    resolve_omni_endpoint,
    resolve_omni_model,
)
from shared.api_openai import is_url
from shared.content import require_dep, require_file, text_error
from shared.env import get_env
from shared.syscmd import find_tool

log = logging.getLogger(__name__)

# mode: "audio" — ASR family (send audio; extract the track from a local video first);
#       "auto"  — AV tools (send video when there are frames, else audio; local videos are
#                 transcoded first — see _preprocess_video).

# Knobs for the size-budget loop in _preprocess_video.
_ENCODE_ATTEMPTS = 3  # a VBV cap can still overshoot slightly; re-encode at the measured ratio
_AUDIO_KBPS = 32  # aac, 16 kHz mono — the track Omni transcribes
_MIN_VIDEO_KBPS = 64  # below this the sampled frames stop being legible; error out instead
_SIZE_AIM = 0.9  # aim under the cap so a mild overshoot still lands inside it
_CRF = 25  # quality target; the bitrate cap only binds when quality alone would exceed the budget

# Audio-only encoding (the ASR family, and the sound track of the frames fallback).
_WAV_BYTES_PER_SEC = 16000 * 2  # what _extract_audio produces: 16 kHz mono s16le
_MP3_KBPS = (16, 24, 32, 40, 48, 64)  # valid 16 kHz MPEG-2 layer-III rates, low → high

# Frames-plus-audio fallback (no OSS). Budgeting is done in ENCODED bytes here, because a frame's
# payload is its base64 data URL; _INLINE_B64_BUDGET keeps the same JSON-framing margin as
# OMNI_MAX_UPLOAD_BYTES does for a single file.
_INLINE_B64_BUDGET = int(OMNI_MAX_B64_BYTES * 0.97)
_AUDIO_SHARE = 0.5  # speech only exists in the sound track, so it gets half the budget…
_AUDIO_SHARE_MAX = 0.7  # …stretched this far when even the floor bitrate needs it
_FRAME_QUALITY = 4  # mjpeg -q:v for the extracted frames (2 = best, 31 = worst)
_B64_BYTES_PER_PIXEL = 0.25  # rough size of one frame at _FRAME_QUALITY, used to pick a resolution
_MIN_FRAME_PIXELS = 160 * 160  # below this a frame stops being readable; thin the list instead


class _InlineBudgetExceeded(RuntimeError):
    """The media cannot be transcoded under the inline budget — the caller should change strategy.

    Distinct from a plain RuntimeError so _local_video_parts can tell "too long to inline" (switch to
    OSS or frames+audio) apart from "ffmpeg is broken" (fall back to the original file).
    """


def _looks_like_video(path: str) -> bool:
    return os.path.splitext(path.split("?", 1)[0])[1].lower() in _VIDEO_EXTS


def _temp_file(suffix: str, *, prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


def _media_duration(path: str) -> float:
    """Container duration in seconds — works for audio-only files too. 0.0 when undeterminable."""
    from shared.video import probe_media

    try:
        return float(probe_media(path).get("format", {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001 — ffprobe missing/unreadable: callers treat 0 as "unknown"
        return 0.0


def _has_audio_stream(path: str) -> bool:
    """True if ``path`` carries an audio stream (False when it cannot be probed)."""
    from shared.video import probe_media

    try:
        return any(s.get("codec_type") == "audio" for s in probe_media(path).get("streams", []))
    except Exception:  # noqa: BLE001 — unprobeable: assume there is nothing to extract
        return False


def _extract_audio(file_path: str, out_path: str) -> None:
    """Pull the audio track to a 16 kHz mono wav (mirrors core asr.py's extraction)."""
    cmd = [
        find_tool("ffmpeg"), "-v", "error", "-y", "-i", file_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path,
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        stderr = proc.stderr.decode().strip() if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg audio extraction failed: {stderr}")


def _encode_audio(file_path: str, out_path: str, *, kbps: int) -> None:
    """Encode the audio track to 16 kHz mono MP3 — a format the endpoint accepts, ~8× smaller than the
    wav _extract_audio produces at the bitrates speech needs."""
    cmd = [
        find_tool("ffmpeg"), "-v", "error", "-y", "-i", file_path,
        "-vn", "-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-ar", "16000", "-ac", "1", out_path,
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        stderr = proc.stderr.decode().strip() if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg audio encode failed: {stderr}")


def _audio_too_long_error(duration: float, max_bytes: int) -> _InlineBudgetExceeded:
    fits_min = max_bytes * _SIZE_AIM * 8 / (_MP3_KBPS[0] * 1000) / 60
    return _InlineBudgetExceeded(
        f"the audio track is too long to upload inline: {duration:.0f}s cannot be encoded at ≥ "
        f"{_MP3_KBPS[0]} kbps within the {max_bytes / 1e6:.1f} MB budget (the endpoint caps an inline "
        f"media item at {OMNI_MAX_B64_BYTES / 1e6:.0f} MB of base64, and encoding inflates the file by "
        f"4/3), which tops out around {fits_min:.0f} min. Trim it, pass an http(s)/OSS URL — remote "
        "media is fetched server-side with no upload limit — or set OSS_AK/OSS_SK/OSS_ENDPOINT/"
        "OSS_BUCKET so oversized media is uploaded automatically."
    )


def _fit_audio(file_path: str, out_path: str, *, budget: int, duration: float) -> None:
    """Encode ``file_path``'s audio into ``out_path`` as 16 kHz mono MP3 that fits ``budget`` bytes.

    Picks the highest bitrate the duration affords and steps down the ladder if the real output still
    overshoots (VBR framing and container overhead make the prediction approximate).
    """
    affordable = [k for k in _MP3_KBPS if duration <= 0 or k * 1000 / 8 * duration <= budget * _SIZE_AIM]
    if not affordable:
        raise _audio_too_long_error(duration, budget)
    for kbps in reversed(affordable):
        _encode_audio(file_path, out_path, kbps=kbps)
        size = os.path.getsize(out_path)
        if size <= budget:
            return
        log.info("audio at %d kbps is %.1f MB (cap %.1f MB); stepping down", kbps, size / 1e6, budget / 1e6)
    raise _audio_too_long_error(duration or _media_duration(out_path), budget)


def _local_audio_part(file_path: str, cleanup: list[str], *, budget: int) -> dict:
    """An ``input_audio`` part for a local file, transcoded only as far as ``budget`` demands.

    An audio file that already fits is sent untouched — that keeps full fidelity, which matters for
    music tagging; anything else is pulled to 16 kHz mono, preferring the lossless wav ASR likes and
    dropping to MP3 only when wav would not fit.
    """
    duration = _media_duration(file_path)
    if not has_video_stream(file_path) and os.path.getsize(file_path) <= budget:
        return omni_audio_part(file_path)
    if 0 < duration and duration * _WAV_BYTES_PER_SEC <= budget:
        wav = _temp_file(".wav", prefix="omni_asr_")
        cleanup.append(wav)
        _extract_audio(file_path, wav)
        return omni_audio_part(wav)
    mp3 = _temp_file(".mp3", prefix="omni_asr_")
    cleanup.append(mp3)
    _fit_audio(file_path, mp3, budget=budget, duration=duration)
    return omni_audio_part(mp3)


def _video_kbps(duration: float, byte_budget: float) -> float:
    """Video bitrate (kbps) that lands ``duration`` seconds of A/V inside ``byte_budget``."""
    return byte_budget * 8 / duration / 1000 - _AUDIO_KBPS


def _too_long_error(duration: float, max_bytes: int) -> _InlineBudgetExceeded:
    fits_min = max_bytes * 8 / ((_MIN_VIDEO_KBPS + _AUDIO_KBPS) * 1000) / 60
    return _InlineBudgetExceeded(
        f"video is too long to upload inline: {duration:.0f}s cannot be encoded at ≥ "
        f"{_MIN_VIDEO_KBPS} kbps within the {max_bytes / 1e6:.1f} MB budget (the endpoint caps an "
        f"inline media item at {OMNI_MAX_B64_BYTES / 1e6:.0f} MB of base64, and encoding inflates the "
        f"file by 4/3), which tops out around {fits_min:.1f} min at this fps"
    )


def _encode_video(
    file_path: str, out_path: str, *, height: int, width: int, fps: float | None, video_kbps: float | None
) -> None:
    """One ffmpeg pass: scale to height×width, optionally cap the frame rate and the video bitrate,
    and downmix the audio to 16 kHz mono (matching _extract_audio).

    Quality is CRF-driven with ``video_kbps`` applied as a VBV *ceiling* rather than an ABR target,
    so a short clip stays small instead of being inflated toward the byte budget.
    """
    cmd = [
        find_tool("ffmpeg"), "-v", "error", "-y", "-i", file_path,
        "-vf", f"scale={width}:{height}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", str(_CRF),
    ]  # fmt: skip
    if fps:
        cmd += ["-r", f"{fps:g}"]
    if video_kbps:
        kbps = max(1, int(video_kbps))
        cmd += ["-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"]
    cmd += ["-c:a", "aac", "-b:a", f"{_AUDIO_KBPS}k", "-ar", "16000", "-ac", "1", out_path]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        stderr = proc.stderr.decode().strip() if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg video preprocess failed: {stderr}")


def _preprocess_video(
    file_path: str,
    out_path: str,
    max_pixels: int,
    *,
    fps: float | None = None,
    max_bytes: int = OMNI_MAX_UPLOAD_BYTES,
) -> None:
    """Transcode a local video for upload, guaranteeing the result fits ``max_bytes``.

    Frames are smart_resize'd into ``max_pixels`` (patch-grid aligned, same math as vision_chat's
    frame path) and the audio goes to 16 kHz mono. On top of that: the frame rate is capped at the
    server's sampling ``fps`` — encoding more frames than Omni will ever sample is pure payload —
    and a bitrate ceiling derived from the duration keeps the file inside the byte budget. A VBV cap
    can still overshoot, so the real output size is verified and the pass repeated at the measured
    ratio. A video too long to fit above ``_MIN_VIDEO_KBPS`` raises ``_InlineBudgetExceeded`` rather
    than silently shipping an over-limit request — the caller then switches to OSS or frames+audio.
    """
    from shared.env import TOKEN_SIZE
    from shared.image import smart_resize
    from shared.video import get_video_info

    info = get_video_info(file_path)
    height, width = smart_resize(info["height"], info["width"], TOKEN_SIZE * TOKEN_SIZE, max_pixels)
    duration = float(info.get("duration") or 0.0)
    rate = min(fps, info["native_fps"]) if fps and info.get("native_fps") else fps

    budget = max_bytes * _SIZE_AIM
    video_kbps = _video_kbps(duration, budget) if duration > 0 else None
    if video_kbps is not None and video_kbps < _MIN_VIDEO_KBPS:
        raise _too_long_error(duration, max_bytes)

    for attempt in range(1, _ENCODE_ATTEMPTS + 1):
        _encode_video(file_path, out_path, height=height, width=width, fps=rate, video_kbps=video_kbps)
        size = os.path.getsize(out_path)
        if size <= max_bytes:
            return
        # Overshot: re-derive the target from what this pass actually produced. When the probe gave
        # no duration, the output tells us — it is the same length.
        if duration <= 0:
            duration = float(get_video_info(out_path).get("duration") or 0.0)
        if duration <= 0:
            raise RuntimeError(
                f"transcoded video is {size / 1e6:.1f} MB, over the {max_bytes / 1e6:.1f} MB upload "
                "budget, and its duration could not be determined to retarget the bitrate"
            )
        video_kbps = (size * 8 / duration / 1000) * (budget / size) - _AUDIO_KBPS
        if video_kbps < _MIN_VIDEO_KBPS:
            raise _too_long_error(duration, max_bytes)
        log.info(
            "transcode attempt %d produced %.1f MB (cap %.1f MB); retrying at %.0f kbps",
            attempt, size / 1e6, max_bytes / 1e6, video_kbps,
        )  # fmt: skip
    raise _InlineBudgetExceeded(
        f"could not transcode the video under the {max_bytes / 1e6:.1f} MB upload budget after "
        f"{_ENCODE_ATTEMPTS} attempts"
    )


def _transcode_and_upload(file_path: str, out_path: str, max_pixels: int, fps: float) -> str:
    """Transcode for sampling — with NO byte budget, a signed URL has none — upload, return the URL.

    Still worth transcoding rather than uploading the original: 448²/1 fps is what Omni samples anyway,
    so this cuts both the upload time and the server-side decode.
    """
    from shared.env import TOKEN_SIZE
    from shared.image import smart_resize
    from shared.video import get_video_info

    info = get_video_info(file_path)
    height, width = smart_resize(info["height"], info["width"], TOKEN_SIZE * TOKEN_SIZE, max_pixels)
    rate = min(fps, info["native_fps"]) if fps and info.get("native_fps") else fps
    _encode_video(file_path, out_path, height=height, width=width, fps=rate, video_kbps=None)
    return oss.upload_and_sign(out_path, key_prefix=get_env("OSS_VIDEO_CLIP_PREFIX", "tmp/video_clips"))


def _thin_to_fit(frames: list[tuple[float, str]], budget: int) -> list[tuple[float, str]]:
    """Uniformly thin an extracted frame list until its base64 fits ``budget``.

    Frame count is the cheapest knob once the frames exist: dropping entries is free, re-encoding them
    at a smaller size is another full extraction pass.
    """
    total = sum(len(b64) for _, b64 in frames)
    if total <= budget or not frames:
        return frames
    keep = max(OMNI_MIN_FRAMES, int(len(frames) * budget / total))
    step = len(frames) / keep
    thinned = [frames[min(len(frames) - 1, int(i * step))] for i in range(keep)]
    # Frames differ in size, so the average can still lie — trim from the end until it really fits.
    while len(thinned) > OMNI_MIN_FRAMES and sum(len(b64) for _, b64 in thinned) > budget:
        thinned.pop()
    log.info("thinned %d frames to %d to fit %.1f MB", len(frames), len(thinned), budget / 1e6)
    return thinned


def _fit_frames(
    file_path: str, duration: float, fps: float, max_pixels: int, budget: int
) -> tuple[list[str], list[float]]:
    """Sample the video uniformly into base64 JPEG frames fitting ``budget`` encoded bytes.

    Returns ``(data URLs, timestamps)``. The resolution is chosen from the per-frame share of the
    budget (never above ``max_pixels``, never below ``_MIN_FRAME_PIXELS``); whatever the estimate got
    wrong is absorbed by thinning the list afterwards, so this extracts exactly once.
    """
    from shared.env import TOKEN_SIZE
    from shared.image import smart_resize
    from shared.video import extract_frames_by_seeking, get_video_info

    info = get_video_info(file_path)
    count = min(OMNI_MAX_B64_FRAMES, max(OMNI_MIN_FRAMES, int(duration * fps)))
    pixels = min(max_pixels, max(_MIN_FRAME_PIXELS, int(budget / count / _B64_BYTES_PER_PIXEL)))
    height, width = smart_resize(info["height"], info["width"], TOKEN_SIZE * TOKEN_SIZE, pixels)

    stamps = [round(i * duration / count, 2) for i in range(count)]
    frames = extract_frames_by_seeking(file_path, stamps, height, width, quality=_FRAME_QUALITY)
    if len(frames) < OMNI_MIN_FRAMES:
        raise RuntimeError(f"could not extract enough frames from {os.path.basename(file_path)} to stand in for it")
    frames.sort(key=lambda f: f[0])
    frames = _thin_to_fit(frames, budget)
    total = sum(len(b64) for _, b64 in frames)
    if total > budget:
        # The frames form needs at least OMNI_MIN_FRAMES images, so there is a floor we cannot go under.
        raise _InlineBudgetExceeded(
            f"even {len(frames)} frames need {total / 1e6:.2f} MB of base64, more than the "
            f"{budget / 1e6:.2f} MB left for them after the audio track; trim the video, or pass an "
            "http(s)/OSS URL — remote media is fetched server-side with no upload limit"
        )
    log.info("split video into %d frames at %d×%d", len(frames), width, height)
    return [jpeg_data_url(b64) for _, b64 in frames], [ts for ts, _ in frames]


def _frames_note(stamps: list[float], duration: float, with_audio: bool) -> dict:
    """The text part that gives the frame list a time base (it has none of its own)."""
    listing = ", ".join(f"#{i + 1}={ts:g}s" for i, ts in enumerate(stamps))
    audio = (
        " The audio content part is the COMPLETE, uninterrupted sound track of the same video — use it, "
        "not the frames, for anything spoken or heard, and trust its continuous timeline."
        if with_audio
        else " This video has no audio track."
    )
    return {
        "type": "text",
        "text": (
            f"The video is {duration:.0f}s long and too large to upload whole, so it is provided as "
            f"{len(stamps)} frames sampled uniformly from it, in chronological order. Each frame's "
            f"timestamp in the original video: {listing}. Express every time reference on the original "
            f"video's timeline using these timestamps — do NOT assume the frames are one second apart."
            f"{audio}"
        ),
    }


def _frames_and_audio_parts(file_path: str, fps: float, max_pixels: int, cleanup: list[str]) -> list[dict]:
    """Decompose a too-long local video into a frame list + its audio track + a timestamp note.

    Omni's image-list video form carries no audio, so the sound track rides along as its own part and
    the model combines them (multi-modal combination in one request needs a Qwen3.5-Omni model). The
    inline budget is split: speech only exists in the sound track, so that is fitted first — up to
    ``_AUDIO_SHARE``, stretched to ``_AUDIO_SHARE_MAX`` when the floor bitrate needs it — and the frames
    take whatever is left.
    """
    duration = _media_duration(file_path)
    if duration <= 0:
        raise RuntimeError(
            "cannot split a video of unknown duration into frames + audio; trim it or pass an http(s) URL"
        )

    audio_part = None
    if _has_audio_stream(file_path):
        mp3 = _temp_file(".mp3", prefix="omni_av_")
        cleanup.append(mp3)
        # Both shares come off the SAME total, expressed in raw bytes (base64 costs 4/3), so the two
        # parts can never add up past the cap.
        share = int(_INLINE_B64_BUDGET * _AUDIO_SHARE * 3 / 4)
        try:
            _fit_audio(file_path, mp3, budget=share, duration=duration)
        except _InlineBudgetExceeded:
            _fit_audio(file_path, mp3, budget=int(_INLINE_B64_BUDGET * _AUDIO_SHARE_MAX * 3 / 4), duration=duration)
        audio_part = omni_audio_part(mp3)
        frames_budget = _INLINE_B64_BUDGET - b64_len(os.path.getsize(mp3))
    else:
        frames_budget = _INLINE_B64_BUDGET

    urls, stamps = _fit_frames(file_path, duration, fps, max_pixels, frames_budget)
    parts = [omni_frames_part(urls)]
    if audio_part:
        parts.append(audio_part)
    parts.append(_frames_note(stamps, duration, audio_part is not None))
    return parts


def _local_video_parts(
    file_path: str, fps: float, max_pixels: int, cleanup: list[str], max_video_sec: float | None, model: str
) -> list[dict]:
    """Route a local video: one fitted file, else an OSS URL, else frames + audio.

    A file longer than the model's server-side video-duration cap can't be sampled whole (inline or by
    signed URL), so it degrades UP FRONT to frames + audio — a bounded image list plus the sound track,
    which the endpoint never samples as a timeline. Within the cap, delivery is unchanged: one fitted
    inline file, else an OSS URL, else (oversized, no OSS) frames + audio.
    """
    from shared.video import video_duration_exceeds

    if video_duration_exceeds(file_path, max_video_sec):
        log.warning(
            "video %s exceeds the server-side duration limit for model %r; sending frames + audio instead",
            file_path,
            model,
        )
        return _frames_and_audio_parts(file_path, fps, max_pixels, cleanup)

    mp4 = _temp_file(".mp4", prefix="omni_av_")
    cleanup.append(mp4)
    try:
        _preprocess_video(file_path, mp4, max_pixels, fps=fps)
    except _InlineBudgetExceeded as e:
        # Too long to inline as one file. Squeezing further would only trade this error for the
        # endpoint's, so change how the video is delivered instead.
        log.info("%s; switching delivery", e)
        if oss.is_upload_configured():
            try:
                url = _transcode_and_upload(file_path, mp4, max_pixels, fps)
            except Exception as upload_error:  # noqa: BLE001 — OSS down/misconfigured: frames still work
                log.warning("OSS upload failed (%s); falling back to frames + audio", upload_error)
            else:
                return [omni_video_part(url, fps=fps, max_pixels=max_pixels)]
        return _frames_and_audio_parts(file_path, fps, max_pixels, cleanup)  # frames: exempt from the cap
    except Exception as e:  # noqa: BLE001 — best-effort: send the original file unprocessed
        log.warning("video preprocess failed (%s)", e)
        # Only fall back when the original already fits: shipping an over-limit payload just trades
        # this error for the endpoint's, minus the actionable hint.
        if os.path.getsize(file_path) > OMNI_MAX_UPLOAD_BYTES:
            raise
        log.warning("sending the original file instead")
        return [omni_video_part(file_path, fps=fps, max_pixels=max_pixels)]
    return [omni_video_part(mp4, fps=fps, max_pixels=max_pixels)]


def _build_media_parts(
    file_path: str,
    mode: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    max_video_sec: float | None,
    model: str,
) -> list[dict]:
    """The real media parts for a live call (may transcode media into temp files → ``cleanup``).

    One part in the common cases; two or three when a local video has to travel as frames + audio.
    """
    if is_url(file_path):
        # Remote media has no upload cap — hand the URL over untouched. A video URL still lets the
        # ASR family transcribe the audio track server-side.
        return (
            [omni_video_part(file_path, fps=fps, max_pixels=max_pixels)]
            if has_video_stream(file_path)
            else [omni_audio_part(file_path)]
        )
    if mode == "audio" or not has_video_stream(file_path):
        return [_local_audio_part(file_path, cleanup, budget=OMNI_MAX_UPLOAD_BYTES)]
    return _local_video_parts(file_path, fps, max_pixels, cleanup, max_video_sec, model)


def _dry_run_blocks(file_path: str, prompt: str, mode: str, fps: float, max_pixels: int, model: str) -> list[dict]:
    """Preview the request WITHOUT reading/extracting the file or hitting the network.

    Media kind is decided by extension here (no ffprobe), so dry_run works offline without ffmpeg.
    """
    kind = (
        "video"
        if (mode == "auto" and _looks_like_video(file_path))
        or (mode == "audio" and is_url(file_path) and _looks_like_video(file_path))
        else ("audio" if mode == "audio" else "video" if _looks_like_video(file_path) else "audio")
    )
    if kind == "video":
        media = {
            "type": "video_url",
            "source": os.path.basename(file_path),
            "fps": fps,
            "max_pixels": max_pixels,
            "note": (
                "<base64/url elided in dry_run> — a local video over the inline cap is instead uploaded "
                "to OSS (when configured) or split into a frames + audio pair"
            ),
        }
    else:
        media = {"type": "input_audio", "source": os.path.basename(file_path), "note": "<base64/url elided in dry_run>"}
    preview = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "modalities": ["text"],
        "messages": [{"role": "user", "content": [media, {"type": "text", "text": prompt}]}],
    }
    return [
        {
            "type": "text",
            "text": "DRY RUN — request that would be sent:\n" + json.dumps(preview, ensure_ascii=False, indent=2),
        }
    ]


def run_omni(
    arguments: dict[str, Any],
    *,
    prompt: str,
    mode: str,
    default_fps: float = DEFAULT_OMNI_FPS,
    default_max_pixels: int = DEFAULT_OMNI_MAX_PIXELS,
    json_output: bool = True,
    max_tokens: int = 4096,
) -> tuple[Any, list[dict] | None]:
    """Validate, route media, and either preview or call Omni.

    Returns ``(parsed_json, None)`` on a successful call, or ``(None, blocks)`` when the tool should
    return ``blocks`` directly — i.e. on a validation error, on ``dry_run``, or on a call failure.
    ``json_output=False`` skips the strict-JSON parse and returns the model's raw text instead
    (for report-style prompts like omni_av_caption's).
    """
    file_path = arguments.get("file_path", "")
    if not is_url(file_path):
        if err := require_file(file_path):
            return None, err
    if err := require_dep("openai"):
        return None, err

    fps = arguments.get("fps") or default_fps
    max_pixels = arguments.get("max_pixels") or default_max_pixels
    model = arguments.get("model") or resolve_omni_model()
    base_url, api_key = resolve_omni_endpoint(arguments)

    if arguments.get("dry_run"):
        return None, _dry_run_blocks(file_path, prompt, mode, fps, max_pixels, model)

    cleanup: list[str] = []
    try:
        parts = _build_media_parts(file_path, mode, fps, max_pixels, cleanup, omni_video_max_sec(model), model)
        if any(p.get("type") == "video" and isinstance(p.get("video"), list) for p in parts) and (
            "qwen3.5-omni" not in model
        ):
            log.warning(
                "model %r may reject a frame list combined with audio — multi-modal combination in one "
                "request is documented for the Qwen3.5-Omni series only",
                model,
            )
        messages = [{"role": "user", "content": [*parts, {"type": "text", "text": prompt}]}]
        if json_output:
            data = call_omni_json(
                base_url=base_url, api_key=api_key, model=model, messages=messages, max_tokens=max_tokens
            )
        else:
            data, _ = call_omni(
                base_url=base_url, api_key=api_key, model=model, messages=messages, max_tokens=max_tokens
            )
        return data, None
    except Exception as e:  # noqa: BLE001 — surface any error as a tool message, not a crash
        return None, text_error(str(e))
    finally:
        for path in cleanup:
            try:
                os.remove(path)
            except OSError:
                pass


# ── Output formatting shared by the timestamped tools ──────────────────────────────────────────────
def language_hint(arguments: dict[str, Any]) -> str:
    lang = arguments.get("language")
    return f" The spoken language is {lang}." if lang else ""


def _coerce_time(value: Any) -> Any:
    """Coerce a timestamp to float seconds; keep the original if it isn't parseable.

    The model sometimes returns start/end as strings ("0.0", "00:05", "12.3s") and sometimes as
    numbers — normalize so every tool's JSON carries numeric timestamps. Reuses shared.video.parse_time
    (numbers / 'SS' / 'MM:SS' / 'HH:MM:SS' / trailing 's').
    """
    from shared.video import parse_time

    try:
        parsed = parse_time(value)
    except Exception:  # noqa: BLE001 — unparseable timestamp: leave it as the model gave it
        return value
    return parsed if parsed is not None else value


def normalize_times(items: list, keys: tuple[str, ...] = ("start", "end")) -> list:
    """Return the segment list with each of ``keys`` coerced to float via _coerce_time."""
    out = []
    for item in items:
        if isinstance(item, dict):
            item = {**item, **{k: _coerce_time(item[k]) for k in keys if k in item}}
        out.append(item)
    return out


def ms_to_srt_time(ms: int) -> str:
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms % 1000:03d}"


def segments_to_srt(segments: list[dict], *, label_key: str | None = None) -> str:
    """Render ``[{start,end,text[,speaker]}]`` as SRT. ``label_key`` prefixes each cue (e.g. speaker)."""
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        try:
            start = float(seg.get("start", 0) or 0)
            end = float(seg.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        body = str(seg.get("text", "")).strip()
        if label_key and seg.get(label_key):
            body = f"[{seg[label_key]}] {body}"
        lines += [str(i), f"{ms_to_srt_time(int(start * 1000))} --> {ms_to_srt_time(int(end * 1000))}", body, ""]
    return "\n".join(lines)


def json_block(data: Any) -> dict:
    return {"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}


def summary_block(msg: str) -> dict:
    return {"type": "text", "text": msg}
