"""Omni ASR — transcribe speech to plain continuous text, no timestamps."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from shared.content import text_error

from ._common import json_block, language_hint, run_omni, summary_block


class OmniAsrArgs(BaseModel):
    file_path: str
    media_type: Literal["auto", "audio", "video"] = "audio"
    language: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dry_run: bool = False


TOOL = {"name": "omni_asr", "args": OmniAsrArgs}

_PROMPT = (
    "Transcribe ALL speech in this media into one continuous, accurately punctuated plain-text "
    "string. Do not add timestamps, speaker labels, or commentary.{lang} "
    'Output STRICTLY this JSON and nothing else: {{"text": "<full transcription>"}}'
)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe all speech in an audio/video file into ONE continuous plain-text string, with no
    timestamps, using the Qwen-Omni model. For timestamps use omni_asr_timestamped; for speaker
    labels use omni_multi_speaker_asr. Defaults to audio-only, extracting the track from local videos
    and HTTP(S) video URLs. Temporary oss:// video URLs are decoded server-side with frames and
    audio intact; provide an audio URL or local file for strictly audio-only processing. Use
    auto/video only when the user explicitly requests synchronized audio-video transcription.
    A local file travels inline, where the endpoint caps a media
    item at 10 MB of base64, so the audio is downmixed to 16 kHz mono and MP3-compressed at a
    duration-fitted bitrate when needed — good for roughly 55 min. For longer media pass an
    http(s)/OSS URL or transcribe in parts.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        media_type: Audio (default) extracts the audio track, except for temporary OSS videos
            that require server-side video decoding. Explicit auto/video enables video
            input with its audio; use only when the user requests audio-video transcription.
        language: Spoken-language hint (zh, en, ja, …). Auto-detected if omitted.
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.8-omni-flash.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    media_type = arguments.get("media_type", "audio")
    if media_type not in {"auto", "audio", "video"}:
        return text_error("media_type must be auto, audio, or video")
    data, blocks = run_omni(arguments, prompt=_PROMPT.format(lang=language_hint(arguments)), mode=media_type)
    if blocks is not None:
        return blocks

    if isinstance(data, dict):
        transcription = str(data.get("text") or data.get("transcript") or "").strip()
    elif isinstance(data, str):
        transcription = data.strip()
    elif isinstance(data, list):
        transcription = " ".join(str(x.get("text", "") if isinstance(x, dict) else x) for x in data).strip()
    else:
        transcription = ""

    return [json_block({"text": transcription}), summary_block(transcription or "(no speech detected)")]
