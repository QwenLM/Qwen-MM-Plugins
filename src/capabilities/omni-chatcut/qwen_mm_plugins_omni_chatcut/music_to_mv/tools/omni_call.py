"""Prompt-driven Qwen Omni audio/video analysis for Music2MV workflows."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.api_omni import (
    OMNI_MAX_UPLOAD_BYTES,
    call_omni,
    call_omni_json,
    omni_audio_part,
    omni_video_part,
    resolve_omni_endpoint,
    resolve_omni_model,
)
from shared.api_openai import is_url
from shared.content import require_dep, text, text_error
from shared.env import get_env
from shared.syscmd import find_tool

from ..model_config import load_model_config


class OmniCallArgs(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    audio_path: str | None = None
    video_path: str | None = None
    prompt: str
    output_format: Literal["text", "json"] = "text"
    temperature: float = Field(default=0.01, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=65536)
    fps: float = Field(default=2.0, gt=0.0, le=10.0)
    max_pixels: int = Field(default=200704, ge=65536, le=1048576)
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_config_path: str | None = None
    dry_run: bool = False


TOOL = {"name": "omni_call", "args": OmniCallArgs}

_MP3_KBPS = (64, 48, 40, 32, 24, 16)
_VIDEO_VARIANTS = (
    (640, 2.0, 30),
    (512, 2.0, 34),
    (448, 1.5, 37),
    (360, 1.0, 40),
)


def _fit_local_audio(source: str, cleanup: list[str]) -> str:
    """Return the original audio when it fits, otherwise a bounded 16 kHz mono MP3."""
    path = Path(source).expanduser().resolve()
    if path.stat().st_size <= OMNI_MAX_UPLOAD_BYTES:
        return str(path)

    fd, fitted = tempfile.mkstemp(prefix="qwen_music2mv_", suffix=".mp3")
    os.close(fd)
    cleanup.append(fitted)
    ffmpeg = find_tool("ffmpeg")
    last_error = ""
    for kbps in _MP3_KBPS:
        completed = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                f"{kbps}k",
                "-ar",
                "16000",
                "-ac",
                "1",
                fitted,
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if completed.returncode != 0:
            last_error = completed.stderr.strip()
            continue
        if Path(fitted).stat().st_size <= OMNI_MAX_UPLOAD_BYTES:
            return fitted
    detail = f": {last_error}" if last_error else ""
    raise ValueError(
        "audio cannot be fitted under the Omni inline upload budget even at 16 kbps; "
        f"trim it or pass a public/OSS URL{detail}"
    )


def _fit_local_video(source: str, cleanup: list[str]) -> str:
    """Return the original short video when it fits, otherwise a bounded MP4 with embedded audio."""
    path = Path(source).expanduser().resolve()
    if path.stat().st_size <= OMNI_MAX_UPLOAD_BYTES:
        return str(path)

    fd, fitted = tempfile.mkstemp(prefix="qwen_music2mv_qc_", suffix=".mp4")
    os.close(fd)
    cleanup.append(fitted)
    ffmpeg = find_tool("ffmpeg")
    last_error = ""
    for height, fps, crf in _VIDEO_VARIANTS:
        completed = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                f"scale=-2:{height}:force_original_aspect_ratio=decrease,fps={fps}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                str(crf),
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-movflags",
                "+faststart",
                fitted,
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if completed.returncode != 0:
            last_error = completed.stderr.strip()
            continue
        if Path(fitted).stat().st_size <= OMNI_MAX_UPLOAD_BYTES:
            return fitted
    detail = f": {last_error}" if last_error else ""
    raise ValueError(
        f"video cannot be fitted under the Omni inline upload budget; trim it or pass a public/OSS URL{detail}"
    )


def run_call(arguments: dict[str, Any]) -> Any:
    """Run the reusable Omni request and return either parsed JSON or raw text."""
    audio_path = arguments.get("audio_path") or ""
    video_path = arguments.get("video_path") or ""
    prompt = arguments.get("prompt", "")
    if not audio_path and not video_path:
        raise ValueError("audio_path or video_path is required")
    if not prompt.strip():
        raise ValueError("prompt is required")
    for source in (audio_path, video_path):
        if source and not is_url(source) and not Path(source).expanduser().is_file():
            raise ValueError(f"not a readable file: {source}")

    model_config, resolved_model_config_path = load_model_config(arguments.get("model_config_path"))
    omni_config = model_config.get("omni") or {}
    if not isinstance(omni_config, dict):
        raise ValueError("model config omni must be a JSON object")
    model = resolve_omni_model(arguments.get("model") or omni_config.get("model"))
    endpoint_arguments = dict(arguments)
    if not endpoint_arguments.get("base_url") and omni_config.get("base_url"):
        endpoint_arguments["base_url"] = omni_config["base_url"]
    configured_key_env = str(omni_config.get("api_key_env") or "").strip()
    if not endpoint_arguments.get("api_key") and configured_key_env:
        configured_key = get_env(configured_key_env, "") or ""
        if not configured_key and not arguments.get("dry_run"):
            raise RuntimeError(f"no Omni API key — set the environment variable {configured_key_env}")
        endpoint_arguments["api_key"] = configured_key or "EMPTY"
    base_url, api_key = resolve_omni_endpoint(endpoint_arguments)
    if arguments.get("dry_run"):
        preview_content = []
        if video_path:
            preview_content.append(
                {
                    "type": "video_url",
                    "source": Path(video_path.split("?", 1)[0]).name,
                    "fps": float(arguments.get("fps", 2.0)),
                    "max_pixels": int(arguments.get("max_pixels", 200704)),
                    "note": "<base64/url elided in dry_run>",
                }
            )
        if audio_path:
            preview_content.append(
                {
                    "type": "input_audio",
                    "source": Path(audio_path.split("?", 1)[0]).name,
                    "note": "<base64/url elided in dry_run>",
                }
            )
        preview_content.append({"type": "text", "text": prompt})
        preview = {
            "base_url": base_url,
            "model": model,
            "stream": True,
            "modalities": ["text"],
            "messages": [{"role": "user", "content": preview_content}],
            "output_format": arguments.get("output_format", "text"),
            "temperature": float(arguments.get("temperature", 0.01)),
            "max_tokens": int(arguments.get("max_tokens", 4096)),
            "has_api_key": bool(api_key and api_key != "EMPTY"),
            "model_config_path": str(resolved_model_config_path) if resolved_model_config_path else None,
        }
        return "DRY RUN — request that would be sent:\n" + json.dumps(preview, ensure_ascii=False, indent=2)

    cleanup: list[str] = []
    try:
        content = []
        if video_path:
            video_source = video_path if is_url(video_path) else _fit_local_video(video_path, cleanup)
            content.append(
                omni_video_part(
                    video_source,
                    fps=float(arguments.get("fps", 2.0)),
                    max_pixels=int(arguments.get("max_pixels", 200704)),
                )
            )
        if audio_path:
            audio_source = audio_path if is_url(audio_path) else _fit_local_audio(audio_path, cleanup)
            content.append(omni_audio_part(audio_source))
        content.append({"type": "text", "text": prompt})
        messages = [
            {
                "role": "user",
                "content": content,
            }
        ]
        common = {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "messages": messages,
            "max_tokens": int(arguments.get("max_tokens", 4096)),
            "temperature": float(arguments.get("temperature", 0.01)),
        }
        if arguments.get("output_format", "text") == "json":
            return call_omni_json(**common)
        result, _ = call_omni(**common)
        return result
    finally:
        for temporary in cleanup:
            try:
                os.remove(temporary)
            except OSError:
                pass


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Analyze audio and/or a short video with Qwen Omni using an arbitrary evidence-gathering prompt.

    Oversized local media is fitted to the endpoint's inline budget with ffmpeg. Use dry_run to
    inspect the request without a network call.

    Args:
        audio_path: Readable local audio path or public HTTP(S) audio URL.
        video_path: Optional readable local video path or public HTTP(S) video URL; embedded audio is
            preserved.
        prompt: Task-specific analysis prompt sent with the supplied media.
        output_format: Return raw model text or require and parse a JSON response.
        temperature: Sampling temperature.
        max_tokens: Maximum completion tokens.
        fps: Video sampling rate for Omni.
        max_pixels: Maximum sampled pixels per video frame.
        model: Omni model override; otherwise unified model config, QWEN_MM_API_OMNI_MODEL, then default.
        api_key: Optional credential override; otherwise the environment variable named by unified
            model config.
        base_url: Optional OpenAI-compatible endpoint override; otherwise unified model config then
            DASHSCOPE_BASE_URL.
        model_config_path: Unified model connection JSON; otherwise the path named by
            QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG.
        dry_run: Preview the request shape without reading media, requiring a key, or calling the
            network.
    """
    if err := require_dep("openai"):
        return err
    try:
        result = run_call(arguments)
        if isinstance(result, str):
            return [text(result)]
        return [text(json.dumps(result, ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001 — tool errors are returned to the MCP caller
        return text_error(str(exc))
