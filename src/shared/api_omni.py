"""Qwen-Omni client (shared): streaming compatible-mode chat + robust JSON + A/V content parts.

The Omni model (default ``qwen3.5-omni-plus``) reads video frames AND the embedded audio track in a
single call, and — unlike the plain VL path in ``api_openai`` — it **must** be called with
``stream=True`` + ``modalities=["text"]`` (+ ``stream_options.include_usage``). This module is the
one place that speaks that protocol, so every Omni tool (the api ``omni/`` subpackage) shares it.

It targets the same OpenAI-compatible endpoint as ``api_openai`` (DashScope compatible-mode is the
default ``base_url``) and reuses that module's endpoint resolution + URL helpers; streaming is done
through the ``openai`` SDK (imported lazily) to stay consistent with the rest of the codebase, with
retry via ``shared.retry.retry_call``. Native async generation still lives in ``api_dashscope``.

Inline media is capped: a local file travels as a base64 ``data:`` URL and the encoded string must
stay under ``OMNI_MAX_B64_BYTES`` (10 MB). This module owns both the budget constants and the gate
that enforces them (see ``inline_b64_bytes`` / ``PayloadTooLargeError``); it offers three shapes for
media — a video file (``omni_video_part``), a frame list (``omni_frames_part``, no audio of its own)
and audio (``omni_audio_part``) — leaving the choice of which to send to the caller.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from shared.api_openai import is_url, resolve_openai_endpoint
from shared.env import get_env

log = logging.getLogger(__name__)

DEFAULT_OMNI_MODEL = "qwen3.5-omni-plus"
DEFAULT_MAX_RETRIES = 4
DEFAULT_RETRY_BACKOFF = 1.0
DEFAULT_OMNI_TIMEOUT = 1800  # streaming A/V completions can run long; overridable via QWEN_MM_CHAT_TIMEOUT

# Default video sampling knobs (must sit at the content-part TOP level to take effect — see
# omni_video_part). 200704 px ≈ 448², matching the reference omni client.
DEFAULT_OMNI_FPS = 1.0
DEFAULT_OMNI_MAX_PIXELS = 200704

# A local file can only travel inline, as a base64 ``data:`` URL (see _data_url), and DashScope caps
# the ENCODED string at 10 MB — the same cap for an image, an audio and a video item. base64 inflates
# the raw bytes by 4/3, so the file itself must stay under 3/4 of that, minus a small margin for the
# mime prefix and JSON framing; callers that transcode before upload size their output against
# OMNI_MAX_UPLOAD_BYTES (see the omni tools' _preprocess_video). Public URLs are exempt — remote media
# is fetched and sampled server-side, up to 2 GB / 1 h of video (Qwen3.5-Omni) — which is why the omni
# tools upload oversized media to OSS, or split it into frames + audio, instead of raising this budget.
OMNI_MAX_B64_BYTES = 10 * 1000 * 1000
OMNI_MAX_UPLOAD_BYTES = int(OMNI_MAX_B64_BYTES * 3 / 4 * 0.97)

# Image-list ("frames") video form — see omni_frames_part. Qwen3.5-Omni takes 2…250 base64 images
# (2048 by URL); the lower models take fewer, so the frames path is Qwen3.5-only in practice.
OMNI_MIN_FRAMES = 2
OMNI_MAX_B64_FRAMES = 250

# Per-model video-duration ceilings for SERVER-SIDE sampling (seconds), from Bailian/Model Studio docs
# (help.aliyun.com/zh/model-studio/qwen-omni, as of 2026-08): Qwen3.5-Omni video ≤ 1 h (audio ≤ 3 h),
# Qwen3-Omni-Flash ≤ 20 min, Qwen-Omni-Turbo ≤ 3 min. Prefix-matched; unknown model → no cap. Only
# relevant where the whole file is sampled server-side (a signed URL).
_OMNI_VIDEO_MAX_SEC: dict[str, int] = {
    "qwen3.5-omni": 3600,
    "qwen3-omni-flash": 20 * 60,
    "qwen-omni-turbo": 3 * 60,
}


def omni_video_max_sec(model: str | None) -> int | None:
    """Server-side video-duration cap (seconds) for an Omni ``model``, or None when unknown."""
    if not model:
        return None
    for prefix, cap in _OMNI_VIDEO_MAX_SEC.items():
        if model.startswith(prefix):
            return cap
    return None


# HTTP statuses worth retrying (mirrors api_openai._RETRYABLE_STATUS).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".flv", ".ts", ".m2ts", ".mpg", ".mpeg"}


class EmptyCompletionError(RuntimeError):
    """Raised when Omni returns an empty completion — treated as a transient, retryable failure."""


class PayloadTooLargeError(ValueError):
    """Raised before the request leaves the process when its inline base64 exceeds the endpoint's cap.

    Failing here rather than at the endpoint keeps the actionable hint (transcode / URL / OSS) instead
    of trading it for an opaque 400 — which is not retryable anyway.
    """


def resolve_omni_endpoint(arguments: dict[str, Any]) -> tuple[str, str]:
    """(base_url, api_key) for an Omni call — same precedence as the OpenAI-compatible path."""
    return resolve_openai_endpoint(arguments)


def _omni_timeout() -> int:
    raw = get_env("QWEN_MM_CHAT_TIMEOUT")
    try:
        return int(raw) if raw else DEFAULT_OMNI_TIMEOUT
    except ValueError:
        log.warning("QWEN_MM_CHAT_TIMEOUT=%r is not a valid integer; using default %d", raw, DEFAULT_OMNI_TIMEOUT)
        return DEFAULT_OMNI_TIMEOUT


# ── Content-part builders ────────────────────────────────────────────────────────────────────────
def b64_len(n_bytes: int) -> int:
    """Length of the base64 encoding of ``n_bytes`` raw bytes (4 chars per 3 bytes, padded)."""
    return 4 * ((n_bytes + 2) // 3)


def _local_b64(source: str) -> tuple[Path, str]:
    """Read and base64-encode a local file, refusing one that cannot fit the endpoint's cap."""
    path = Path(source)
    raw = path.read_bytes()
    encoded_len = b64_len(len(raw))
    if encoded_len > OMNI_MAX_B64_BYTES:
        raise PayloadTooLargeError(
            f"{path.name} is {len(raw) / 1e6:.1f} MB — {encoded_len / 1e6:.1f} MB once base64-encoded, "
            f"over the {OMNI_MAX_B64_BYTES / 1e6:.0f} MB cap the endpoint applies to an inline media "
            "item. Transcode it smaller, pass an http(s) URL, or configure OSS_* so it can be uploaded."
        )
    return path, base64.b64encode(raw).decode("ascii")


def _data_url(source: str, default_mime: str, *, omit_mime: bool = False) -> str:
    """Base64 ``data:`` URL for a local file, refusing one that cannot fit the endpoint's cap.

    ``omit_mime`` emits the bare ``data:;base64,`` form the DashScope docs use for ``input_audio``,
    where the media type is carried by the part's own ``format`` field instead.
    """
    path, encoded = _local_b64(source)
    mime, _ = mimetypes.guess_type(path.name)
    prefix = "" if omit_mime else (mime or default_mime)
    return f"data:{prefix};base64,{encoded}"


def omni_video_part(source: str, *, fps: float = DEFAULT_OMNI_FPS, max_pixels: int = DEFAULT_OMNI_MAX_PIXELS) -> dict:
    """A video content part for Omni: URL/data-URL passthrough or a local file base64'd.

    ``fps`` and ``max_pixels`` are placed at the part's TOP level — the Omni endpoint only honors
    them there, not inside ``video_url`` (verified against the reference omni client).
    """
    url = source if is_url(source) else _data_url(source, "video/mp4")
    return {"type": "video_url", "video_url": {"url": url}, "fps": fps, "max_pixels": max_pixels}


def omni_frames_part(frames: list[str]) -> dict:
    """The image-list video form: ``{"type": "video", "video": [<image>, …]}``.

    Each entry is an image URL or a base64 ``data:`` URL of one frame; Omni treats the list as a video
    but — unlike the file form — it carries NO audio, so pair it with ``omni_audio_part`` whenever the
    source had a sound track (combining modalities in one request needs a Qwen3.5-Omni model).
    Frames must be in chronological order; tell the model their timestamps in the text part, since the
    list itself has no time base.
    """
    if not OMNI_MIN_FRAMES <= len(frames) <= OMNI_MAX_B64_FRAMES:
        raise ValueError(f"the frames form takes {OMNI_MIN_FRAMES}…{OMNI_MAX_B64_FRAMES} images, got {len(frames)}")
    return {"type": "video", "video": frames}


def jpeg_data_url(encoded: str) -> str:
    """Wrap an already-base64'd JPEG (as shared.video's frame extractors return) as a ``data:`` URL."""
    return f"data:image/jpeg;base64,{encoded}"


def omni_audio_part(source: str, *, audio_format: str | None = None) -> dict:
    """An audio content part for Omni (OpenAI-compatible ``input_audio``).

    A local file becomes the bare ``data:;base64,…`` form the DashScope docs show for audio (the type
    is carried by ``format``, not a mime prefix).

    That wrapper is DashScope-specific: OpenAI's spec — and vLLM, which implements it — expects
    ``data`` to be RAW base64. Against a self-hosted server the wrapped form fails base64 decoding
    ("Incorrect padding"), so ``QWEN_MM_AUDIO_RAW_B64=1`` sends the encoded bytes directly. Both
    forms go through the same local-file size guard.
    """
    fmt = (audio_format or Path(source).suffix.lstrip(".") or "wav").lower()
    if is_url(source):
        data = source
    elif (get_env("QWEN_MM_AUDIO_RAW_B64") or "").lower() in ("1", "true", "yes", "on"):
        _, data = _local_b64(source)
    else:
        data = _data_url(source, "audio/wav", omit_mime=True)
    return {"type": "input_audio", "input_audio": {"data": data, "format": fmt}}


def inline_b64_bytes(messages: list[dict[str, Any]]) -> int:
    """Total base64 payload carried inline by ``messages`` (every ``data:`` URL in every part)."""
    total = 0

    def _count(value: Any) -> None:
        nonlocal total
        if isinstance(value, str):
            if value.startswith("data:"):
                total += len(value) - value.find(",") - 1
        elif isinstance(value, dict):
            for item in value.values():
                _count(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                _count(item)

    _count(messages)
    return total


def has_video_stream(path: str) -> bool:
    """True if ``path`` carries a real (non-cover-art) video stream. URLs fall back to extension."""
    if is_url(path):
        return Path(path.split("?", 1)[0]).suffix.lower() in _VIDEO_EXTS
    try:
        from shared.video import probe_media

        for stream in probe_media(path).get("streams", []):
            if stream.get("codec_type") != "video":
                continue
            if stream.get("disposition", {}).get("attached_pic"):
                continue  # album art / thumbnail embedded in an audio file
            return True
        return False
    except Exception:  # noqa: BLE001 — ffprobe missing/unreadable: fall back to the extension
        return Path(path).suffix.lower() in _VIDEO_EXTS


def text_msg(role: str, text: str) -> dict:
    """A plain text chat message."""
    return {"role": role, "content": [{"type": "text", "text": text}]}


# ── Streaming call ───────────────────────────────────────────────────────────────────────────────
def call_omni(
    *,
    base_url: str,
    api_key: str,
    model: str = DEFAULT_OMNI_MODEL,
    messages: list[dict[str, Any]],
    max_tokens: int = 65536,
    temperature: float = 0.7,
    max_retries: int = DEFAULT_MAX_RETRIES,
    extra_body: dict[str, Any] | None = None,
) -> tuple[str, Any]:
    """Call the Omni model (streaming) and return ``(text, usage)``.

    Enforces the required ``stream=True`` + ``modalities=["text"]`` + ``stream_options`` protocol,
    accumulates the streamed text deltas, and retries transient failures (typed openai errors,
    retryable HTTP statuses, and an empty completion) via ``shared.retry.retry_call``.

    The inline base64 across all parts is checked once up front: an over-cap request is rejected here
    (with the hint on how to shrink it) rather than sent to be 400'd — this is the single gate every
    media path funnels through, whatever content parts the caller assembled.
    """
    import openai
    from openai import OpenAI

    from shared.retry import retry_call

    if api_key in ("", "EMPTY") and "dashscope" in base_url:
        raise RuntimeError("no API key — set DASHSCOPE_API_KEY (or pass api_key)")

    inline = inline_b64_bytes(messages)
    if inline > OMNI_MAX_B64_BYTES:
        raise PayloadTooLargeError(
            f"the request carries {inline / 1e6:.1f} MB of inline base64 media, over the endpoint's "
            f"{OMNI_MAX_B64_BYTES / 1e6:.0f} MB cap. Send fewer/smaller media items, pass an http(s) "
            "URL, or configure OSS_* so oversized media is uploaded instead of inlined."
        )

    body = {"modalities": ["text"]}
    if extra_body:
        body.update(extra_body)

    retryable = (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.APIConnectionError,
        openai.InternalServerError,
    )

    def _is_transient(e: Exception) -> bool:
        return isinstance(e, (EmptyCompletionError, *retryable)) or (
            isinstance(e, openai.APIStatusError) and getattr(e, "status_code", None) in _RETRYABLE_STATUS
        )

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=_omni_timeout())

    def _once() -> tuple[str, Any]:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=body,
        )
        parts: list[str] = []
        usage = None
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            for choice in getattr(chunk, "choices", None) or []:
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None) if delta else None
                if content:
                    parts.append(content)
        text = "".join(parts)
        if not text.strip():
            raise EmptyCompletionError("omni returned an empty completion")
        return text, usage

    return retry_call(
        _once,
        attempts=max_retries,
        base_backoff=DEFAULT_RETRY_BACKOFF,
        mode="exp",
        cap=60.0,
        should_retry=_is_transient,
        on_exhausted="raise",
        log=log,
    )


# ── Robust JSON parsing (ports the reference omni client's extract_json / call_json) ───────────────
def extract_json(text: str) -> Any:
    """Parse JSON out of a model reply: strip code fences, slice to the outermost brackets, then
    tolerate trailing commas and (if available) JSON5. Raises ``ValueError`` if nothing parses."""
    if not text or not text.strip():
        raise ValueError("cannot parse JSON from an empty string")

    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    raw = (m.group(1) if m else text).strip()

    # Slice from the first opening bracket to the last matching closing one.
    starts = [i for i in (raw.find("{"), raw.find("[")) if i != -1]
    if starts:
        start = min(starts)
        end = max(raw.rfind("}"), raw.rfind("]"))
        if end > start:
            raw = raw[start : end + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    repaired = re.sub(r",(\s*[}\]])", r"\1", raw)  # drop trailing commas
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    try:
        import json5  # optional dependency

        return json5.loads(raw)
    except Exception as e:  # noqa: BLE001 — json5 absent or still unparseable
        raise ValueError(f"could not parse JSON from model output: {e}") from e


def call_omni_json(
    *,
    base_url: str,
    api_key: str,
    model: str = DEFAULT_OMNI_MODEL,
    messages: list[dict[str, Any]],
    max_tokens: int = 4096,
    temperature: float = 0.3,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """``call_omni`` + robust JSON parse, with ONE LLM-repair round if the first reply won't parse."""
    text, _ = call_omni(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        max_retries=max_retries,
    )
    try:
        return extract_json(text)
    except ValueError:
        log.info("omni JSON parse failed; attempting one LLM repair round")
        repair = [
            text_msg("system", "You fix malformed JSON. Output ONLY valid JSON, preserving the original semantics."),
            text_msg("user", f"Fix this into valid JSON, output JSON only:\n{text}"),
        ]
        fixed, _ = call_omni(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=repair,
            max_tokens=max_tokens,
            temperature=0.0,
            max_retries=max_retries,
        )
        return extract_json(fixed)
