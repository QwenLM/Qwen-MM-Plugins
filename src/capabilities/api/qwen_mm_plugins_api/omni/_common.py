"""Shared request and formatting helpers for the API plugin's specialized Omni tools.

Media preparation lives in :mod:`shared.omni_media`. This module intentionally keeps thin private
adapters with the historical names so existing tests and callers can override individual delivery
steps without duplicating the implementation.
"""

from __future__ import annotations

import json
import os
from typing import Any

from shared import omni_media, oss  # noqa: F401 — compatibility surface for callers/tests
from shared.api_omni import (
    DEFAULT_OMNI_FPS,
    DEFAULT_OMNI_MAX_PIXELS,
    OMNI_MAX_B64_BYTES,
    OMNI_MAX_UPLOAD_BYTES,
    call_omni,
    call_omni_json,
    has_video_extension,
    has_video_stream,
    is_omni_url,
    omni_audio_part,
    omni_video_max_sec,
    omni_video_part,
    resolve_omni_endpoint,
    resolve_omni_model,
)
from shared.content import require_dep, require_file, text_error
from shared.syscmd import find_tool  # noqa: F401 — compatibility surface for reachability tests

_INLINE_B64_BUDGET = omni_media.INLINE_B64_BUDGET
_InlineBudgetExceeded = omni_media.InlineBudgetExceeded


# Compatibility adapters. Keep these small: media policy belongs in shared.omni_media.
def _extract_audio(file_path: str, out_path: str) -> None:
    omni_media.extract_audio(file_path, out_path)


def _encode_audio(
    file_path: str,
    out_path: str,
    *,
    kbps: int,
    start_time: float = 0.0,
    duration: float | None = None,
) -> None:
    omni_media.encode_audio(
        file_path,
        out_path,
        kbps=kbps,
        start_time=start_time,
        duration=duration,
    )


def _fit_audio(
    file_path: str,
    out_path: str,
    *,
    budget: int,
    duration: float,
    start_time: float = 0.0,
) -> None:
    omni_media.fit_audio(
        file_path,
        out_path,
        budget=budget,
        duration=duration,
        start_time=start_time,
        encode_audio_fn=_encode_audio,
    )


def _local_audio_part(file_path: str, cleanup: list[str], *, budget: int) -> dict:
    return omni_media.local_audio_part(file_path, cleanup, budget=budget)


def _preprocess_video(
    file_path: str,
    out_path: str,
    max_pixels: int,
    *,
    fps: float | None = None,
    max_bytes: int = OMNI_MAX_UPLOAD_BYTES,
    start_time: float = 0.0,
    duration: float | None = None,
) -> None:
    omni_media.preprocess_video(
        file_path,
        out_path,
        max_pixels,
        fps=fps,
        max_bytes=max_bytes,
        start_time=start_time,
        duration=duration,
    )


def _transcode_and_upload(
    file_path: str,
    out_path: str,
    max_pixels: int,
    fps: float,
    *,
    start_time: float = 0.0,
    duration: float | None = None,
) -> str:
    return omni_media.transcode_and_upload(
        file_path,
        out_path,
        max_pixels,
        fps,
        start_time=start_time,
        duration=duration,
    )


def _fit_frames(
    file_path: str,
    duration: float,
    fps: float,
    max_pixels: int,
    budget: int,
    *,
    start_time: float = 0.0,
) -> tuple[list[str], list[float]]:
    return omni_media.fit_frames(
        file_path,
        duration,
        fps,
        max_pixels,
        budget,
        start_time=start_time,
    )


def _frames_and_audio_parts(
    file_path: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    *,
    start_time: float = 0.0,
    duration: float | None = None,
) -> list[dict]:
    return omni_media.frames_and_audio_parts(
        file_path,
        fps,
        max_pixels,
        cleanup,
        start_time=start_time,
        duration=duration,
        inline_b64_budget=_INLINE_B64_BUDGET,
        fit_audio_fn=_fit_audio,
        fit_frames_fn=_fit_frames,
    )


def _local_video_parts(
    file_path: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    max_video_sec: float | None,
    model: str,
) -> list[dict]:
    return omni_media.local_video_parts(
        file_path,
        fps,
        max_pixels,
        cleanup,
        max_video_sec,
        model,
        max_upload_bytes=OMNI_MAX_UPLOAD_BYTES,
        preprocess_video_fn=_preprocess_video,
        frames_and_audio_parts_fn=_frames_and_audio_parts,
        transcode_and_upload_fn=_transcode_and_upload,
    )


def _temporary_oss_parts(
    file_path: str,
    mode: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    max_video_sec: float | None,
    model: str,
    base_url: str,
    api_key: str,
) -> list[dict] | None:
    return omni_media.temporary_oss_parts(
        file_path,
        mode,
        fps,
        max_pixels,
        cleanup,
        max_video_sec,
        model,
        base_url,
        api_key,
        max_b64_bytes=OMNI_MAX_B64_BYTES,
        encode_audio_fn=_encode_audio,
    )


def _build_media_parts(
    file_path: str,
    mode: str,
    fps: float,
    max_pixels: int,
    cleanup: list[str],
    max_video_sec: float | None,
    model: str,
    base_url: str,
    api_key: str,
) -> list[dict]:
    if is_omni_url(file_path):
        return (
            [omni_video_part(file_path, fps=fps, max_pixels=max_pixels)]
            if has_video_stream(file_path)
            else [omni_audio_part(file_path)]
        )
    temporary = _temporary_oss_parts(
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
        return [_local_audio_part(file_path, cleanup, budget=OMNI_MAX_UPLOAD_BYTES)]
    return _local_video_parts(file_path, fps, max_pixels, cleanup, max_video_sec, model)


def _dry_run_blocks(file_path: str, prompt: str, mode: str, fps: float, max_pixels: int, model: str) -> list[dict]:
    """Preview a request without reading media, invoking ffmpeg, or calling the model."""
    if has_video_extension(file_path) and (mode != "audio" or is_omni_url(file_path)):
        media = {
            "type": "video_url",
            "source": os.path.basename(file_path),
            "fps": fps,
            "max_pixels": max_pixels,
            "note": (
                "<base64/url elided in dry_run> — a local video over the inline cap is first offered "
                "through DashScope temporary OSS, then configured OSS or a frames + audio fallback"
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
    max_tokens: int = 65536,
) -> tuple[Any, list[dict] | None]:
    """Validate, prepare media, and call Omni for a specialized atomic tool."""
    file_path = arguments.get("file_path", "")
    if not is_omni_url(file_path):
        if error := require_file(file_path):
            return None, error
    if error := require_dep("openai"):
        return None, error

    fps = arguments.get("fps") or default_fps
    max_pixels = arguments.get("max_pixels") or default_max_pixels
    model = resolve_omni_model(arguments.get("model"))
    base_url, api_key = resolve_omni_endpoint(arguments)

    if arguments.get("dry_run"):
        return None, _dry_run_blocks(file_path, prompt, mode, fps, max_pixels, model)

    cleanup: list[str] = []
    try:
        parts = _build_media_parts(
            file_path,
            mode,
            fps,
            max_pixels,
            cleanup,
            omni_video_max_sec(model),
            model,
            base_url,
            api_key,
        )
        messages = [{"role": "user", "content": [*parts, {"type": "text", "text": prompt}]}]
        if json_output:
            data = call_omni_json(
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
        else:
            data, _usage = call_omni(
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
        return data, None
    except Exception as exc:  # noqa: BLE001 — MCP tools return errors instead of crashing
        return None, text_error(str(exc))
    finally:
        omni_media.cleanup_files(cleanup)


def language_hint(arguments: dict[str, Any]) -> str:
    lang = arguments.get("language")
    return f" The spoken language is {lang}." if lang else ""


def _coerce_time(value: Any) -> Any:
    """Coerce a timestamp to seconds, retaining values that cannot be parsed."""
    from shared.video import parse_time

    try:
        parsed = parse_time(value)
    except Exception:  # noqa: BLE001 — retain the model value when it is not parseable
        return value
    return parsed if parsed is not None else value


def normalize_times(items: list, keys: tuple[str, ...] = ("start", "end")) -> list:
    """Return items with the requested timestamp fields coerced to float seconds."""
    normalized = []
    for item in items:
        if isinstance(item, dict):
            item = {**item, **{key: _coerce_time(item[key]) for key in keys if key in item}}
        normalized.append(item)
    return normalized


def ms_to_srt_time(ms: int) -> str:
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms % 1000:03d}"


def segments_to_srt(segments: list[dict], *, label_key: str | None = None) -> str:
    """Render timestamped segments as SRT, optionally prefixing each cue with a label."""
    lines: list[str] = []
    for index, segment in enumerate(segments, 1):
        try:
            start = float(segment.get("start", 0) or 0)
            end = float(segment.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        body = str(segment.get("text", "")).strip()
        if label_key and segment.get(label_key):
            body = f"[{segment[label_key]}] {body}"
        lines += [
            str(index),
            f"{ms_to_srt_time(int(start * 1000))} --> {ms_to_srt_time(int(end * 1000))}",
            body,
            "",
        ]
    return "\n".join(lines)


def json_block(data: Any) -> dict:
    return {"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}


def summary_block(message: str) -> dict:
    return {"type": "text", "text": message}
