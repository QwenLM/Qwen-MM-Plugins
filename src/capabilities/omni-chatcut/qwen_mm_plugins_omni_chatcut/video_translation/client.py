"""Lightweight client for an external IndexTTS2/Demucs/TEN-VAD service."""

from __future__ import annotations

import base64
import errno
import json
import logging
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from shared.env import get_env
from shared.retry import retry_call
from shared.syscmd import which_tool

log = logging.getLogger(__name__)
SERVER_ENV = "QWEN_MM_DUBBING_SERVER_URL"


class _CurlResponse:
    def __init__(self, body: bytes):
        self.body = body

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("curl fallback returned an invalid JSON response") from exc


def server_url(explicit: str | None = None) -> str:
    value = (explicit or get_env(SERVER_ENV, refresh_config=True) or "").strip().rstrip("/")
    if not value:
        raise ValueError(f"dubbing service URL is not configured; set {SERVER_ENV}")
    return value


def _requests():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is required for the dubbing service client") from exc
    return requests


def _is_bad_file_descriptor(exc: BaseException) -> bool:
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, "errno", None) == errno.EBADF or "Bad file descriptor" in str(current):
            return True
        for linked in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(linked, BaseException):
                pending.append(linked)
        pending.extend(item for item in getattr(current, "args", ()) if isinstance(item, BaseException))
    return False


def _curl_request(method: str, url: str, *, timeout: float, **kwargs) -> _CurlResponse:
    curl = which_tool("curl")
    if not curl:
        raise RuntimeError("Python networking failed with EBADF and the curl fallback is unavailable")
    unsupported = set(kwargs) - {"json", "headers"}
    if unsupported:
        raise TypeError(f"curl fallback does not support request options: {', '.join(sorted(unsupported))}")
    payload = kwargs.get("json")
    headers = dict(kwargs.get("headers") or {})
    command = [
        curl,
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--request",
        method.upper(),
        "--connect-timeout",
        str(min(float(timeout), 30.0)),
        "--max-time",
        str(float(timeout)),
        "--header",
        "Accept: application/json",
    ]
    for name, value in headers.items():
        command.extend(["--header", f"{name}: {value}"])
    with tempfile.TemporaryDirectory(prefix="qwen-dubbing-http-") as temporary:
        if payload is not None:
            body_path = Path(temporary) / "request.json"
            body_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            command.extend(["--header", "Content-Type: application/json", "--data-binary", f"@{body_path}"])
        command.append(url)
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=float(timeout) + 5,
        )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl fallback request failed: {detail or 'unknown curl error'}")
    return _CurlResponse(completed.stdout)


def _request(method: str, url: str, **kwargs):
    requests = _requests()
    timeout = kwargs.pop("timeout", 900)

    def call():
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            if not _is_bad_file_descriptor(exc):
                raise
            log.warning("Python socket connect failed with EBADF; retrying through curl")
            return _curl_request(method, url, timeout=timeout, **kwargs)

    return retry_call(call, attempts=3, base_backoff=2, mode="exp", cap=10, log=log)


def _encoded(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return base64.b64encode(path.read_bytes()).decode("ascii")


def health(explicit_server: str | None = None) -> dict[str, Any]:
    response = _request("GET", f"{server_url(explicit_server)}/health", timeout=30)
    data = response.json()
    return {"ready": bool(data.get("model_loaded")), "status": data.get("status", "unknown")}


def separate_audio(
    input_path: str,
    output_dir: str,
    *,
    explicit_server: str | None = None,
    model: str = "htdemucs",
) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    response = _request(
        "POST",
        f"{server_url(explicit_server)}/separate",
        json={
            "audio_base64": _encoded(source),
            "audio_filename": source.name,
            "model": model,
            "two_stems": "vocals",
            "mp3": False,
            "return_audio": True,
        },
    )
    data = response.json()
    encoded = data.get("stems_base64") or {}
    if not {"vocals", "no_vocals"} <= set(encoded):
        raise RuntimeError("dubbing service did not return vocals and no_vocals stems")
    destination.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ("vocals", "no_vocals"):
        path = destination / f"{name}.wav"
        path.write_bytes(base64.b64decode(encoded[name], validate=True))
        paths[name] = str(path)
    return {"stems": paths, "infer_time_sec": data.get("infer_time_sec")}


def detect_speech(
    input_path: str,
    *,
    explicit_server: str | None = None,
    threshold: float = 0.5,
    hop_size: int = 256,
    min_speech: float = 0.2,
    min_silence: float = 0.3,
    pad: float = 0.1,
) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    response = _request(
        "POST",
        f"{server_url(explicit_server)}/vad",
        json={
            "audio_base64": _encoded(source),
            "audio_filename": source.name,
            "separate_first": False,
            "threshold": threshold,
            "hop_size": hop_size,
            "min_speech": min_speech,
            "min_silence": min_silence,
            "pad": pad,
        },
    )
    data = response.json()
    duration = data.get("duration")
    sample_rate = data.get("sample_rate")
    segments = data.get("segments")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise RuntimeError("dubbing service returned an invalid VAD duration")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise RuntimeError("dubbing service returned an invalid VAD sample rate")
    if not isinstance(segments, list):
        raise RuntimeError("dubbing service returned invalid VAD segments")
    normalized_segments: list[dict[str, float]] = []
    previous_end = 0.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise RuntimeError(f"dubbing service returned an invalid VAD segment at index {index}")
        start = segment.get("start_time")
        end = segment.get("end_time")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or end > duration + 0.05
            or start < previous_end
        ):
            raise RuntimeError(f"dubbing service returned an invalid VAD interval at index {index}")
        normalized_segments.append({"start_time": float(start), "end_time": float(end)})
        previous_end = float(end)
    return {
        "duration_sec": float(duration),
        "sample_rate": sample_rate,
        "segments": normalized_segments,
        "infer_time_sec": data.get("infer_time_sec"),
    }


def synthesize_speech(
    *,
    text: str,
    reference_audio: str,
    output_path: str,
    explicit_server: str | None = None,
) -> dict[str, Any]:
    reference = Path(reference_audio).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not text.strip():
        raise ValueError("text is required")
    response = _request(
        "POST",
        f"{server_url(explicit_server)}/tts",
        json={
            "text": text,
            "voice_base64": _encoded(reference),
            "voice_filename": reference.name,
            "trim_silence": True,
            "trim_ref_silence": True,
            "return_audio": True,
        },
    )
    data = response.json()
    audio = data.get("audio_base64")
    if not audio:
        raise RuntimeError("dubbing service returned no audio")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64decode(audio, validate=True))
    return {
        "output_path": str(output),
        "duration_sec": data.get("duration_sec"),
        "sample_rate": data.get("sample_rate"),
        "infer_time_sec": data.get("infer_time_sec"),
    }
