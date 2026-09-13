"""Expose the API capability's preferred general-purpose Omni chat interface."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.api_omni import (
    call_omni,
    is_omni_url,
    omni_video_max_sec,
    resolve_omni_endpoint,
    resolve_omni_model,
)
from shared.content import require_dep, require_file, text_error
from shared.omni_media import build_media_parts, cleanup_files, normalize_local_range


class PerceiveMediaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    file_path: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    media_type: Literal["auto", "audio", "video"] = "auto"
    start_time: float | None = Field(default=None, ge=0.0)
    end_time: float | None = Field(default=None, gt=0.0)
    fps: float = Field(default=1.0, gt=0.0, le=4.0)
    max_pixels: int = Field(default=200704, ge=65536, le=1048576)
    max_tokens: int = Field(default=65536, ge=256, le=65536)
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    dry_run: bool = False


TOOL = {"name": "perceive_media", "args": PerceiveMediaArgs}


def _dry_run(arguments: dict[str, Any], model: str) -> list[dict[str, str]]:
    preview = {
        "model": model,
        "file_path": arguments["file_path"],
        "media_type": arguments.get("media_type", "auto"),
        "start_time": arguments.get("start_time"),
        "end_time": arguments.get("end_time"),
        "fps": arguments.get("fps", 1.0),
        "max_pixels": arguments.get("max_pixels", 200704),
        "max_tokens": arguments.get("max_tokens", 65536),
        "prompt": arguments["prompt"],
        "note": "No media was read and no model request was sent.",
    }
    return [{"type": "text", "text": json.dumps(preview, ensure_ascii=False, indent=2)}]


def _interval_note(requested_start: float, requested_end: float, actual_end: float) -> dict[str, str]:
    clamped = actual_end < requested_end
    detail = "; end clamped to the media duration" if clamped else ""
    return {
        "type": "text",
        "text": (
            f"Media interval: requested [{requested_start:g}s, {requested_end:g}s], processed "
            f"[{requested_start:g}s, {actual_end:g}s]{detail}. Model-visible timestamps are relative "
            f"to the processed interval; add {requested_start:g}s to map them to the source timeline."
        ),
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Chat with a configured Omni model about audio, video, or joint audio-video evidence.

    The Agent supplies the complete task in `prompt`. The plugin sends that prompt unchanged in one
    model request. A call may cover the whole source or a selected interval of a local source, so the
    Agent can iteratively perceive long media instead of always processing it end to end.

    Args:
        file_path: Absolute local media path or public HTTP(S)/OSS URL.
        prompt: Complete instruction or question supplied by the calling Agent.
        media_type: Auto-detect media, or force audio/video handling for extensionless remote URLs.
        start_time: Optional start of a local-media interval in seconds. Use together with end_time.
            The selected interval is rebased to 0 for model-visible timestamps.
        end_time: Optional end of a local-media interval in seconds. Must be greater than start_time.
        fps: Video sampling rate sent to the model, from above 0 to 4. Default 1.
        max_pixels: Maximum sampled pixels per video frame, from 65536 to 1048576. Default 200704.
        max_tokens: Maximum output tokens, from 256 to 65536. Default 65536.
        model: Model override. Defaults to QWEN_MM_API_OMNI_MODEL, then the shared Omni default.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible endpoint override. Credentials are resolved from shared config.
        dry_run: Return resolved request settings without reading media or calling the model.
    """
    source = str(arguments.get("file_path") or "").strip()
    prompt = str(arguments.get("prompt") or "").strip()
    if not source:
        return text_error("file_path is required")
    if not prompt:
        return text_error("prompt is required")
    start_value = arguments.get("start_time")
    end_value = arguments.get("end_time")
    if (start_value is None) != (end_value is None):
        return text_error("start_time and end_time must be provided together")
    start_time = float(start_value or 0.0)
    end_time = float(end_value) if end_value is not None else None
    if end_time is not None and end_time <= start_time:
        return text_error("end_time must be greater than start_time")
    if end_time is not None and is_omni_url(source):
        return text_error("time-range perception currently requires a local media file")
    if not arguments.get("dry_run") and not is_omni_url(source):
        if error := require_file(source):
            return error
    requested_end_time = end_time
    duration = end_time - start_time if end_time is not None else None
    if end_time is not None and not arguments.get("dry_run"):
        try:
            start_time, duration = normalize_local_range(source, start_time, duration)
        except ValueError as exc:
            return text_error(str(exc))
        end_time = start_time + duration if duration is not None else end_time
    if not arguments.get("dry_run"):
        if error := require_dep("openai"):
            return error

    model = resolve_omni_model(arguments.get("model"))
    if arguments.get("dry_run"):
        return _dry_run({**arguments, "file_path": source, "prompt": prompt}, model)

    base_url, api_key = resolve_omni_endpoint(arguments)
    media_type = str(arguments.get("media_type") or "auto")
    fps = float(arguments.get("fps", 1.0))
    max_pixels = int(arguments.get("max_pixels", 200704))
    max_tokens = int(arguments.get("max_tokens", 65536))
    cleanup: list[str] = []
    try:
        parts = build_media_parts(
            source,
            media_type,
            fps,
            max_pixels,
            cleanup,
            omni_video_max_sec(model),
            model,
            base_url=base_url,
            api_key=api_key,
            start_time=start_time,
            duration=duration,
        )
        text, _usage = call_omni(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{"role": "user", "content": [*parts, {"type": "text", "text": prompt}]}],
            max_tokens=max_tokens,
        )
        blocks = [{"type": "text", "text": text.strip()}]
        if requested_end_time is not None and end_time is not None:
            blocks.append(_interval_note(start_time, requested_end_time, end_time))
        return blocks
    except Exception as exc:  # noqa: BLE001 — MCP tools return actionable errors instead of crashing
        return text_error(f"{type(exc).__name__}: {exc}")
    finally:
        cleanup_files(cleanup)
