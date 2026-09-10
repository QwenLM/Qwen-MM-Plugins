"""Omni Controllable ASR — transcription with sentence- or word-level timestamps."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from ._common import json_block, language_hint, normalize_times, run_omni, segments_to_srt, summary_block


class OmniAsrTimestampedArgs(BaseModel):
    file_path: str
    granularity: Literal["sentence", "word"] = "sentence"
    format: Literal["json", "srt"] = "json"
    language: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dry_run: bool = False


TOOL = {"name": "omni_asr_timestamped", "args": OmniAsrTimestampedArgs}

_PROMPT = (
    "Transcribe ALL speech in this media at {gran}-level granularity, with an accurate start and end "
    "time (in seconds from the media start) for every {gran}.{lang} "
    "Output STRICTLY this JSON and nothing else: "
    '{{"segments": [{{"start": <sec>, "end": <sec>, "text": "<{gran} text>"}}]}}'
)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe speech with controllable-granularity timestamps (sentence- or word-level) using the
    Qwen-Omni model. Returns segments with start/end seconds plus an SRT rendering. A local file
    travels inline, where the endpoint caps a media item at 10 MB of base64, so the audio is
    downmixed to 16 kHz mono and MP3-compressed at a duration-fitted bitrate when needed — good for
    roughly 55 min. For longer media pass an http(s)/OSS URL or transcribe in parts.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        granularity: Timestamp granularity: 'sentence' (default) or 'word'.
        format: Primary output: 'json' (default) or 'srt'.
        language: Spoken-language hint (zh, en, ja, …). Auto-detected if omitted.
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.5-omni-plus.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    gran = arguments.get("granularity", "sentence")
    prompt = _PROMPT.format(gran=gran, lang=language_hint(arguments))
    data, blocks = run_omni(arguments, prompt=prompt, mode="audio")
    if blocks is not None:
        return blocks

    if isinstance(data, list):
        segments = data
    elif isinstance(data, dict):
        segments = data.get("segments") or data.get("results") or []
    else:
        segments = []
    segments = normalize_times([s for s in segments if isinstance(s, dict)])

    result = {"granularity": gran, "segments": segments}
    out: list[dict[str, Any]] = [json_block(result)]
    if segments:
        srt = segments_to_srt(segments)
        if arguments.get("format") == "srt":
            out = [{"type": "text", "text": srt}, json_block(result)]
        else:
            out.append({"type": "text", "text": srt})
    else:
        out.append(summary_block("(no speech detected)"))
    return out
