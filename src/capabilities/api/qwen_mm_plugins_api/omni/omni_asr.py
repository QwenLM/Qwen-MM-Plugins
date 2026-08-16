"""Omni ASR — transcribe speech to plain continuous text, no timestamps."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from ._common import json_block, language_hint, run_omni, summary_block


class OmniAsrArgs(BaseModel):
    file_path: str = Field(description="Absolute path to a local audio/video file, or an http(s)/OSS URL.")
    language: Optional[str] = Field(
        default=None, description="Spoken-language hint (zh, en, ja, …). Auto-detected if omitted."
    )
    model: Optional[str] = Field(
        default=None,
        description="Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.5-omni-plus.",
    )
    api_key: Optional[str] = Field(default=None, description="DashScope API key (defaults to DASHSCOPE_API_KEY).")
    base_url: Optional[str] = Field(default=None, description="OpenAI-compatible base URL override.")
    dry_run: bool = Field(default=False, description="Return the request that would be sent, without calling the API.")


TOOL: dict[str, Any] = {
    "name": "omni_asr",
    "description": (
        "Transcribe all speech in an audio/video file into ONE continuous plain-text string, with no "
        "timestamps, using the Qwen-Omni model. For timestamps use omni_asr_timestamped; for speaker "
        "labels use omni_multi_speaker_asr. "
        "A local file travels inline, where the endpoint caps a media item at 10 MB of base64, so the "
        "audio is downmixed to 16 kHz mono and MP3-compressed at a duration-fitted bitrate when needed "
        "— good for roughly 55 min. For longer media pass an http(s)/OSS URL or transcribe in parts."
    ),
    "args": OmniAsrArgs,
}

_PROMPT = (
    "Transcribe ALL speech in this media into one continuous, accurately punctuated plain-text "
    "string. Do not add timestamps, speaker labels, or commentary.{lang} "
    'Output STRICTLY this JSON and nothing else: {{"text": "<full transcription>"}}'
)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    data, blocks = run_omni(arguments, prompt=_PROMPT.format(lang=language_hint(arguments)), mode="audio")
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
