"""OpenAI-compatible chat client (shared): endpoint resolution + chat call with retry.

Used by the vision server's vision_chat / ocr / grounding. Targets any OpenAI-compatible endpoint
(DashScope's compatible-mode is only the default base_url). The `openai` SDK is imported lazily so
tool discovery stays cheap. DashScope native-REST generation lives in `api_dashscope`.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from shared.env import DEFAULT_DASHSCOPE_BASE_URL, get_env

if TYPE_CHECKING:
    from PIL.Image import Image

log = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0


def resolve_vl_model(model: str | None = None) -> str:
    """Resolve the VL model at call time.

    Precedence: explicit argument → QWEN_MM_API_VL_MODEL → DEFAULT_MODEL.
    """
    return model or get_env("QWEN_MM_API_VL_MODEL") or DEFAULT_MODEL


# Request timeout (seconds) for a chat call — generous for long vision prompts, but bounded so a
# hung connection can't pin a tool call for an hour. Overridable via QWEN_MM_CHAT_TIMEOUT.
DEFAULT_CHAT_TIMEOUT = 600

# A local file can only travel inline as a base64 ``data:`` URL, and DashScope caps the ENCODED
# string at 10 MB per media item. Over that the item must become a URL instead — an http(s) one from
# the user's own OSS bucket, or a model-bound temporary ``oss://`` object (see shared.dashscope_upload).
VL_MAX_B64_BYTES = 10 * 1000 * 1000

# Per-model video-duration ceilings for SERVER-SIDE sampling (seconds), from Bailian/Model Studio docs
# (help.aliyun.com/zh/model-studio/vision, as of 2026-08). Prefix-matched against the model id; a model
# with no entry is treated as "unknown" → no cap (the endpoint still enforces its own limit). Only
# relevant on the OSS-upload path, where the whole video is sampled server-side.
_VL_VIDEO_MAX_SEC: dict[str, int] = {
    "qwen3.7-plus": 2 * 3600,  # flagship: up to 2 h / 2 GB
    "qwen-vl-max": 20 * 60,  # 2 s – 20 min, ≤ 1 GB
    "qwen3-vl": 20 * 60,
}


def vl_video_max_sec(model: str | None) -> int | None:
    """Server-side video-duration cap (seconds) for a VL ``model``, or None when unknown."""
    if not model:
        return None
    for prefix, cap in _VL_VIDEO_MAX_SEC.items():
        if model.startswith(prefix):
            return cap
    return None


def _chat_timeout() -> int:
    """QWEN_MM_CHAT_TIMEOUT (seconds), read at call time; unset/bad values fall back to the default."""
    raw = get_env("QWEN_MM_CHAT_TIMEOUT")
    try:
        return int(raw) if raw else DEFAULT_CHAT_TIMEOUT
    except ValueError:
        log.warning("QWEN_MM_CHAT_TIMEOUT=%r is not a valid integer; using default %d", raw, DEFAULT_CHAT_TIMEOUT)
        return DEFAULT_CHAT_TIMEOUT


# HTTP statuses worth retrying for OpenAI-compatible endpoints.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Request-validation statuses that may mean a best-effort provider hint is unsupported. They are
# handled outside the transient retry loop so the request changes before it is sent again.
_OPTIONAL_FIELD_REJECTION_STATUS = frozenset({400, 422})

_API_KEY_ENV_BY_HOST: dict[str, str] = {
    "dashscope.aliyuncs.com": "DASHSCOPE_API_KEY",
    "dashscope-intl.aliyuncs.com": "DASHSCOPE_API_KEY",
    "api.orcarouter.ai": "ORCAROUTER_API_KEY",
    "openrouter.ai": "OPENROUTER_API_KEY",
}


def resolve_openai_endpoint(arguments: dict[str, Any]) -> tuple[str, str]:
    """Resolve (base_url, api_key) for an OpenAI-compatible call.

    URL precedence: explicit argument → DASHSCOPE_BASE_URL → default. An explicit
    api_key wins; otherwise use the host's API key environment variable. Unlisted hosts
    and missing keys fall back to "EMPTY" for local servers.
    """
    base_url = arguments.get("base_url") or get_env("DASHSCOPE_BASE_URL") or DEFAULT_DASHSCOPE_BASE_URL
    key_env = _API_KEY_ENV_BY_HOST.get(urlsplit(base_url).hostname or "")
    api_key = arguments.get("api_key") or (get_env(key_env) if key_env else None) or "EMPTY"
    return base_url, api_key


def expand_video_frames(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Send sampled video frames as ordered, standard ``image_url`` content parts.

    Preserve the frame rate, video URLs, and other media without mutating the caller's messages.
    """
    prepared = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            prepared.append(message)
            continue
        parts = []
        for part in content:
            if part.get("type") != "video" or not isinstance(part.get("video"), list):
                parts.append(part)
                continue
            fps = f" at {part['fps']} fps" if part.get("fps") else ""
            parts.append({"type": "text", "text": f"Video frames in chronological order{fps}:"})
            parts.extend({"type": "image_url", "image_url": {"url": frame}} for frame in part["video"])
        prepared.append({**message, "content": parts})
    return prepared


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "data:"))


def is_model_url(value: str) -> bool:
    """URLs an endpoint resolves itself, including DashScope temporary ``oss://`` objects."""
    return is_url(value) or value.startswith("oss://")


def b64_len(n_bytes: int) -> int:
    """Length of the base64 encoding of ``n_bytes`` raw bytes (4 chars per 3 bytes, padded)."""
    return 4 * ((n_bytes + 2) // 3)


def _temporary_oss_url(path: str | Path, base_url: str | None, api_key: str | None, model: str | None) -> str | None:
    """Upload ``path`` to DashScope's model-bound temporary OSS, or None when that is unavailable."""
    if base_url is None or api_key is None:
        return None
    from shared import dashscope_upload

    if not dashscope_upload.is_available(base_url, api_key):
        return None
    return dashscope_upload.try_upload_temporary_file(
        path, base_url=base_url, api_key=api_key, model=resolve_vl_model(model)
    )


def _frame_sampling_would_cap(source: str, max_frames: int) -> bool:
    """Whether sampling ``source`` locally would hit ``max_frames`` and so lose temporal detail.

    ``compute_dynamic_fps`` clamps the frame count to ``max_frames``, so a video short enough that
    ``duration * DEFAULT_FPS`` fits under that cap is represented in full by local frames and an
    upload buys nothing.
    """
    from shared.env import DEFAULT_FPS
    from shared.video import get_video_info

    try:
        duration = float(get_video_info(source).get("duration") or 0.0)
    except Exception:  # noqa: BLE001 — unreadable locally: let the upload path try
        return True
    return duration > 0 and int(duration * DEFAULT_FPS) > max_frames


def encode_image_source(
    source: str | Image,
    *,
    allow_upload: bool = True,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Encode a path, URL, or prepared PIL image.

    A local image FILE whose base64 form would exceed the endpoint's per-item ``VL_MAX_B64_BYTES``
    cap is handed over as a DashScope temporary ``oss://`` object instead, when ``base_url``/
    ``api_key`` make that available (see shared.dashscope_upload). Without those the oversized item
    still travels inline, exactly as before, and the endpoint rejects it. ``allow_upload=False``
    suppresses the upload (used by ``dry_run`` so a preview never touches the network).

    A prepared PIL image always travels inline and retains its exact dimensions: callers pass one
    precisely because the model must see the same pixels they measured (see vl/grounding.py), so
    substituting the original file would change those pixels.
    """
    if not isinstance(source, str):
        from shared.image import encode_image

        _, encoded, mime_type = encode_image(source)
        return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}
    if is_model_url(source):
        return {"type": "image_url", "image_url": {"url": source}}
    path = Path(source)
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    if allow_upload and b64_len(path.stat().st_size) > VL_MAX_B64_BYTES:
        url = _temporary_oss_url(path, base_url, api_key, model)
        if url:
            return {"type": "image_url", "image_url": {"url": url}}
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def encode_video_source(
    source: str,
    max_frames: int = 128,
    *,
    allow_upload: bool = True,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """OpenAI-style video content part: a URL passthrough, an OSS upload, or a local file sampled
    into frames.

    Routing for a local video mirrors the Omni path (``shared.omni_media.build_media_parts``), so both
    walk the same ladder and a local video reaches the endpoint whole whenever it can:

    1. DashScope's model-bound temporary OSS, when ``base_url``/``api_key`` reach an endpoint that
       offers it (see shared.dashscope_upload) — no bucket to configure, objects expire in ~48 h.
       Because it needs no configuration it is live by default, so it is used only when local frames
       would hit ``max_frames`` and lose detail;
    2. the user's own OSS bucket, when ``shared.oss.is_upload_configured()``;
    3. local frame sampling, capped at ``max_frames`` inline images.

    Either upload hands the file over as a ``video_url`` that the endpoint samples server-side, lifting
    the inline frame cap. Server-side sampling caps duration per ``model``, so a local file longer than
    that cap skips both uploads and degrades to local frame sampling (sparse for very long clips, but it
    still returns a result). ``allow_upload=False`` suppresses both uploads (used by ``dry_run`` so a
    preview never touches the network).
    """
    if is_model_url(source):
        return {"type": "video_url", "video_url": {"url": source}}

    if allow_upload:
        from shared import dashscope_upload, oss

        temporary = base_url is not None and api_key is not None and dashscope_upload.is_available(base_url, api_key)
        configured = oss.is_upload_configured()
        if temporary or configured:
            from shared.video import video_duration_exceeds

            if video_duration_exceeds(source, vl_video_max_sec(model)):
                log.warning(
                    "video %s exceeds the server-side duration limit for model %r; sampling frames "
                    "locally instead of uploading",
                    source,
                    model or DEFAULT_MODEL,
                )
            else:
                # Temporary storage needs no configuration, so it is live for every DashScope key.
                # Spend the upload only when local frames would lose detail; a configured bucket is
                # an explicit opt-in and keeps its unconditional behavior below.
                if temporary and _frame_sampling_would_cap(source, max_frames):
                    url = _temporary_oss_url(source, base_url, api_key, model)
                    if url:
                        return {"type": "video_url", "video_url": {"url": url}}
                if configured:
                    url = oss.upload_and_sign(source, key_prefix=get_env("OSS_VIDEO_CLIP_PREFIX", "tmp/video_clips"))
                    return {"type": "video_url", "video_url": {"url": url}}

    from shared.env import DEFAULT_FPS, TOKEN_SIZE, VIDEO_MIN_PIXELS
    from shared.image import smart_resize
    from shared.video import compute_dynamic_fps, extract_frames_by_seeking, get_video_info

    info = get_video_info(source)
    target_h, target_w = smart_resize(info["height"], info["width"], VIDEO_MIN_PIXELS, 1280 * TOKEN_SIZE**2)
    fps, nframes = compute_dynamic_fps(info["duration"], info["native_fps"], 4, max_frames, DEFAULT_FPS)
    frame_interval = info["duration"] / nframes if nframes > 0 else 0
    timestamps = [i * frame_interval for i in range(nframes)]
    frames = extract_frames_by_seeking(source, timestamps, target_h, target_w)
    frame_urls = [f"data:image/jpeg;base64,{b64}" for _, b64 in frames]
    return {"type": "video", "video": frame_urls, "fps": round(fps, 2)}


def call_openai_chat(
    *,
    base_url: str,
    api_key: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    optional_extra_body: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Call OpenAI-compatible chat completions, retrying transient failures.

    Retries on the SDK's typed transient errors (rate limit, timeout,
    connection, 5xx) and on retryable HTTP status codes, rather than matching
    substrings of the error message.

    ``optional_extra_body`` carries droppable provider hints. A 400/422 response retries once
    without them; transient failures retry the unchanged request. The base ``extra_body`` is never
    dropped and wins on key conflicts.

    A request carrying a DashScope temporary ``oss://`` resource also sends
    ``X-DashScope-OssResourceResolve: enable``, without which the endpoint cannot read the object.
    """
    import openai
    from openai import OpenAI

    from shared.retry import retry_call

    retryable = (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.APIConnectionError,
        openai.InternalServerError,
    )

    def _is_transient(e: Exception) -> bool:
        return isinstance(e, retryable) or (
            isinstance(e, openai.APIStatusError) and getattr(e, "status_code", None) in _RETRYABLE_STATUS
        )

    base_extra_body = kwargs.get("extra_body") or {}
    if "messages" in kwargs:
        kwargs["messages"] = expand_video_frames(kwargs["messages"])
    from shared.dashscope_upload import OSS_RESOLVE_HEADER, contains_temporary_oss_url

    if contains_temporary_oss_url(kwargs.get("messages")):
        kwargs["extra_headers"] = {**OSS_RESOLVE_HEADER, **(kwargs.get("extra_headers") or {})}
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=_chat_timeout())

    def _create(hints: dict[str, Any] | None) -> Any:
        call_kwargs = dict(kwargs)
        if hints:
            call_kwargs["extra_body"] = {**hints, **base_extra_body}
        return retry_call(
            lambda: client.chat.completions.create(**call_kwargs),
            attempts=max_retries,
            base_backoff=DEFAULT_RETRY_BACKOFF,
            mode="linear",
            should_retry=_is_transient,
            on_exhausted="raise",
            log=log,
        )

    try:
        return _create(optional_extra_body)
    except openai.APIStatusError as e:
        if not optional_extra_body or getattr(e, "status_code", None) not in _OPTIONAL_FIELD_REJECTION_STATUS:
            raise
        log.warning(
            "endpoint rejected optional request field(s) %s with HTTP %s; retrying without them",
            ", ".join(sorted(optional_extra_body)),
            e.status_code,
        )
        return _create(None)
