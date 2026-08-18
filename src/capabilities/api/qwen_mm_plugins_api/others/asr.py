"""MCP tool: transcribe audio/video via DashScope Qwen3-ASR.

Falls back to a self-hosted ASR server (env ASR_SERVER_URLS, comma-separated) when DashScope fails.
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from shared.content import text_error
from shared.env import get_env
from shared.paths import path_to_file_uri
from shared.syscmd import find_tool

log = logging.getLogger(__name__)

MAX_CHUNK_SECONDS = 60
_ASR_RETRIES = 10  # attempts for the self-hosted fallback server
_rr = itertools.count()  # round-robin across self-hosted ASR endpoints


class TranscribeAudioArgs(BaseModel):
    file_path: str = Field(description="Absolute path to a local audio or video file.")
    model: str = Field(
        default="qwen3-asr-flash",
        description="ASR model. Default: qwen3-asr-flash (supports local files, ≤5min per chunk).",
    )
    start_time: Optional[float] = Field(default=None, description="Start time in seconds. Default: 0.")
    end_time: Optional[float] = Field(
        default=None, description="End time in seconds. Default: end of file. Max range: 1 hour."
    )
    format: Literal["srt", "text", "json"] = Field(
        default="srt",
        description="Output format: srt (timestamped subtitles), text (plain text), json (raw).",
    )
    language: Optional[str] = Field(
        default=None,
        description=(
            "Language hint for better accuracy (zh, en, ja, ko, fr, de, es, ru, …). Auto-detected if omitted."
        ),
    )
    enable_itn: bool = Field(
        default=True, description="Inverse text normalization: format numbers/dates/times. Default: true."
    )
    api_key: Optional[str] = Field(default=None, description="DashScope API key (defaults to DASHSCOPE_API_KEY).")


TOOL: dict[str, Any] = {
    "name": "transcribe_audio",
    "description": (
        "Transcribe speech from an audio or video file using DashScope Qwen3-ASR "
        "(27 languages, automatic language detection). "
        "Extracts audio automatically from video files. Supports time range selection. "
        "Returns timestamped subtitles in SRT, plain text, or raw JSON format. "
        "Long files are automatically chunked into segments for processing."
    ),
    "args": TranscribeAudioArgs,
}


def _get_duration(file_path: str) -> float:
    result = subprocess.run(
        [find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", file_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    info = json.loads(result.stdout)
    return float(info.get("format", {}).get("duration", 0))


def _extract_audio(file_path: str, out_path: str, start_time: float = 0, duration: float | None = None) -> None:
    cmd = [find_tool("ffmpeg"), "-v", "error", "-y"]
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    cmd += ["-i", file_path]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        stderr = proc.stderr.decode().strip() if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg failed: {stderr}")


def _local_urls() -> list[str]:
    raw = get_env("ASR_SERVER_URLS") or ""
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def _transcribe_local(audio_path: str, urls: list[str]) -> list[str]:
    """Transcribe via a self-hosted ASR server, round-robined across urls, with retries."""
    from shared.retry import retry_call

    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    url = urls[next(_rr) % len(urls)]
    body = json.dumps({"audio": f"data:audio/wav;base64,{b64}", "return_time_stamps": False}).encode()

    def _post() -> list[str]:
        req = urllib.request.Request(
            f"{url}/asr", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            results = json.loads(resp.read().decode()).get("results", [])
        return [(r.get("text") or "").strip() for r in results if (r.get("text") or "").strip()]

    return retry_call(_post, attempts=_ASR_RETRIES, log=log)


def _transcribe_dashscope(
    audio_path: str, model: str, api_key: str, asr_options: dict[str, Any] | None = None
) -> list[str]:
    import dashscope

    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": [{"audio": path_to_file_uri(audio_path)}]}],
        "result_format": "message",
        "api_key": api_key,
    }
    if asr_options:
        call_kwargs["asr_options"] = asr_options

    resp = dashscope.MultiModalConversation.call(**call_kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"ASR failed ({resp.status_code}): {resp.code} - {resp.message}")

    sentences = []
    for item in resp.output.get("choices", [{}])[0].get("message", {}).get("content", []):
        text = (item.get("text", "") if isinstance(item, dict) else item if isinstance(item, str) else "").strip()
        if text:
            sentences.append(text)
    return sentences


def _transcribe_chunk(
    audio_path: str, model: str, api_key: str, asr_options: dict[str, Any] | None = None
) -> list[str]:
    """DashScope first; on any failure fall back to a self-hosted server (ASR_SERVER_URLS)."""
    urls = _local_urls()
    if not api_key:
        if not urls:
            raise RuntimeError("No API key and no ASR_SERVER_URLS configured")
        return _transcribe_local(audio_path, urls)
    try:
        return _transcribe_dashscope(audio_path, model, api_key, asr_options)
    except Exception as e:  # noqa: BLE001
        if not urls:
            raise
        log.warning("DashScope ASR failed (%s); falling back to %s", e, urls)
        return _transcribe_local(audio_path, urls)


def _ms_to_srt_time(ms: int) -> str:
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    frac = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{frac:03d}"


def _format_srt(chunks: list[tuple[float, float, list[str]]]) -> str:
    lines = []
    idx = 0
    for start_s, end_s, sentences in chunks:
        for text in sentences:
            idx += 1
            lines.append(f"{idx}")
            lines.append(f"{_ms_to_srt_time(int(start_s * 1000))} --> {_ms_to_srt_time(int(end_s * 1000))}")
            lines.append(text)
            lines.append("")
    return "\n".join(lines)


def _format_text(chunks: list[tuple[float, float, list[str]]]) -> str:
    parts = []
    for _, _, sentences in chunks:
        parts.extend(sentences)
    return "\n".join(parts)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    file_path = arguments.get("file_path", "")
    if not os.path.isfile(file_path):
        return text_error(f"file not found: {file_path}")
    media_path = os.path.realpath(file_path)

    model = arguments.get("model") or "qwen3-asr-flash"
    api_key = arguments.get("api_key") or get_env("DASHSCOPE_API_KEY") or ""
    if not api_key and not _local_urls():
        return text_error(
            "no API key. Set DASHSCOPE_API_KEY or pass api_key, or set ASR_SERVER_URLS for self-hosted fallback."
        )

    output_format = arguments.get("format", "srt")
    start_time = arguments.get("start_time", 0.0) or 0.0
    end_time = arguments.get("end_time")

    asr_options: dict[str, Any] = {"enable_itn": arguments.get("enable_itn", True)}
    if arguments.get("language"):
        asr_options["language"] = arguments["language"]

    tmp_dir = tempfile.mkdtemp(prefix="asr_chunks_")
    try:
        total_duration = _get_duration(media_path)
        if total_duration <= 0:
            return text_error("cannot determine file duration.")

        if start_time < 0:
            start_time = 0.0
        if start_time >= total_duration:
            return text_error(f"start_time ({start_time}s) >= duration ({total_duration:.1f}s)")
        if end_time is not None:
            if end_time <= start_time:
                return text_error(f"end_time ({end_time}s) must be > start_time ({start_time}s)")
            end_time = min(end_time, total_duration)

        segment_duration = (end_time if end_time is not None else total_duration) - start_time
        if segment_duration > 3600:
            return text_error(f"segment ({segment_duration:.0f}s) exceeds 1 hour. Use start_time/end_time.")

        n_chunks = max(1, math.ceil(segment_duration / MAX_CHUNK_SECONDS))
        chunk_duration = segment_duration / n_chunks

        all_chunks: list[tuple[float, float, list[str]]] = []

        for i in range(n_chunks):
            chunk_start = start_time + i * chunk_duration
            chunk_dur = min(chunk_duration, segment_duration - i * chunk_duration)
            chunk_end = chunk_start + chunk_dur

            chunk_path = os.path.join(tmp_dir, f"chunk_{i:04d}.wav")
            _extract_audio(media_path, chunk_path, chunk_start, chunk_dur)

            sentences = _transcribe_chunk(chunk_path, model, api_key, asr_options)
            if sentences:
                all_chunks.append((chunk_start, chunk_end, sentences))

            if n_chunks > 1:
                log.info("Chunk %d/%d done", i + 1, n_chunks)

        if not all_chunks:
            return [{"type": "text", "text": "Transcription completed but no speech detected."}]

        time_range = ""
        if start_time > 0 or end_time is not None:
            e = end_time if end_time is not None else total_duration
            time_range = f" [{start_time:.1f}s–{e:.1f}s]"
        summary = (
            f"Transcribed: {os.path.basename(file_path)}{time_range} | "
            f"{segment_duration:.1f}s | {n_chunks} chunk(s) | model: {model}"
        )

        if output_format == "json":
            body = json.dumps(
                [{"start": s, "end": e, "sentences": sents} for s, e, sents in all_chunks],
                indent=2,
                ensure_ascii=False,
            )
        elif output_format == "text":
            body = _format_text(all_chunks)
        else:
            body = _format_srt(all_chunks)

        return [{"type": "text", "text": f"{summary}\n\n{body}"}]

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
