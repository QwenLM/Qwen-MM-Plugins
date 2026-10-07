"""Omni ASR — utterance timestamps from numbered SRT captions."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from shared.content import text_error

from ._common import json_block, language_hint, output_warnings, run_omni, segments_to_srt, summary_block
from ._srt_transcript import parse_srt_transcript


class OmniAsrTimestampedArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str
    media_type: Literal["auto", "audio", "video"] = "audio"
    format: Literal["json", "srt"] = "json"
    language: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dry_run: bool = False


TOOL = {"name": "omni_asr_timestamped", "args": OmniAsrTimestampedArgs}

_PROMPT = "Write SRT captions including the numeric counter and timecodes for speech."


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe speech with utterance-level timestamps using the Qwen-Omni model. The model returns
    numbered SRT captions, which the plugin parses into segments with start/end seconds and an SRT
    rendering. Missing hours/counters/separators are tolerated. Partial or unparseable model output
    returns the recoverable segments or original text with warnings. Timestamp granularity is fixed
    to utterances. Defaults to audio-only, extracting the track from local videos and HTTP(S) video
    URLs. Temporary oss:// video URLs are decoded server-side with frames and audio intact; provide
    an audio URL or local file for strictly audio-only processing. Use auto/video only when the user
    explicitly requests synchronized audio-video transcription. A local file
    travels inline, where the endpoint caps a media item at 10 MB of base64, so the audio is
    downmixed to 16 kHz mono and MP3-compressed at a duration-fitted bitrate when needed — good for
    roughly 55 min. For longer media pass an http(s)/OSS URL or transcribe in parts.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        media_type: Audio (default) extracts the audio track, except for temporary OSS videos
            that require server-side video decoding. Explicit auto/video enables video
            input with its audio; use only when the user requests audio-video transcription.
        format: Primary output: 'json' (default) or 'srt'.
        language: Spoken-language hint (zh, en, ja, …). Auto-detected if omitted.
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.8-omni-flash.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    prompt = _PROMPT + language_hint(arguments)
    media_type = arguments.get("media_type", "audio")
    if media_type not in {"auto", "audio", "video"}:
        return text_error("media_type must be auto, audio, or video")
    text, blocks = run_omni(arguments, prompt=prompt, mode=media_type, json_output=False)
    if blocks is not None:
        return blocks

    warnings = []
    try:
        segments = parse_srt_transcript(text, warnings=warnings)
    except ValueError as exc:
        return [summary_block(text), *output_warnings([f"Could not parse SRT transcript: {exc}"])]

    result = {"granularity": "utterance", "segments": segments}
    out: list[dict[str, Any]] = [json_block(result)]
    if segments:
        srt = segments_to_srt(segments)
        if arguments.get("format") == "srt" and srt:
            out = [{"type": "text", "text": srt}, json_block(result)]
        elif srt:
            out.append({"type": "text", "text": srt})
    else:
        out.append(summary_block("(no speech detected)"))
    return out + output_warnings(warnings, raw_text=text if warnings else None)
