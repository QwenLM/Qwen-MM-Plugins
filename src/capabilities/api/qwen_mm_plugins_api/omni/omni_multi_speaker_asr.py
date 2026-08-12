"""Omni Multi-Speaker ASR — diarized transcription with speaker labels and timestamps."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ._common import json_block, language_hint, normalize_times, run_omni, segments_to_srt, summary_block


class OmniMultiSpeakerAsrArgs(BaseModel):
    file_path: str = Field(description="Absolute path to a local audio/video file, or an http(s)/OSS URL.")
    num_speakers: Optional[int] = Field(
        default=None, description="Expected number of speakers, if known — a hint to guide diarization."
    )
    format: Literal["json", "srt"] = Field(default="json", description="Primary output: 'json' (default) or 'srt'.")
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
    "name": "omni_multi_speaker_asr",
    "description": (
        "Transcribe multi-speaker speech with speaker diarization: distinguishes and labels different "
        "speakers, with start/end timestamps per segment, using the Qwen-Omni model. Returns diarized "
        "segments plus an SRT rendering with speaker tags. "
        "A local file travels inline, where the endpoint caps a media item at 10 MB of base64, so the "
        "audio is downmixed to 16 kHz mono and MP3-compressed at a duration-fitted bitrate when needed "
        "— good for roughly 55 min. For longer media pass an http(s)/OSS URL or transcribe in parts."
    ),
    "args": OmniMultiSpeakerAsrArgs,
}

_PROMPT = (
    "Transcribe ALL speech in this media and perform speaker diarization: assign each segment to a "
    'consistent speaker label (e.g. "Speaker 1", "Speaker 2") and give an accurate start and end '
    "time in seconds.{speakers}{lang} "
    "Output STRICTLY this JSON and nothing else: "
    '{{"segments": [{{"speaker": "<label>", "start": <sec>, "end": <sec>, "text": "<text>"}}]}}'
)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    n = arguments.get("num_speakers")
    speakers_hint = f" There are {n} distinct speakers." if n else ""
    prompt = _PROMPT.format(speakers=speakers_hint, lang=language_hint(arguments))
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
    speakers = sorted({str(s["speaker"]) for s in segments if s.get("speaker")})

    result = {"speakers": speakers, "segments": segments}
    out: list[dict[str, Any]] = [json_block(result)]
    if segments:
        srt = segments_to_srt(segments, label_key="speaker")
        if arguments.get("format") == "srt":
            out = [{"type": "text", "text": srt}, json_block(result)]
        else:
            out.append({"type": "text", "text": srt})
    else:
        out.append(summary_block("(no speech detected)"))
    return out
