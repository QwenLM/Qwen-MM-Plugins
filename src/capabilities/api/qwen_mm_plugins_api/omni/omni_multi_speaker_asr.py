"""Omni Multi-Speaker ASR — diarized transcription with speaker labels and timestamps."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from shared.content import text_error

from ._common import json_block, language_hint, output_warnings, run_omni, segments_to_srt, summary_block
from ._prompts import MULTI_SPEAKER_PROMPT
from ._speaker_transcript import parse_speaker_transcript


class OmniMultiSpeakerAsrArgs(BaseModel):
    file_path: str
    num_speakers: Optional[int] = Field(default=None, gt=0)
    media_type: Literal["auto", "audio", "video"] = "audio"
    fps: Optional[float] = Field(default=None, gt=0.0, le=15.0, allow_inf_nan=False)
    max_pixels: Optional[int] = None
    format: Literal["json", "srt"] = "json"
    language: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dry_run: bool = False


TOOL = {"name": "omni_multi_speaker_asr", "args": OmniMultiSpeakerAsrArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe multi-speaker speech with speaker diarization: distinguishes and labels different
    speakers, with start/end timestamps per segment, using the Qwen-Omni model. Returns diarized
    segments plus an SRT rendering with speaker tags. The model uses the recommended speaker-token
    output, which the plugin parses locally. Missing wrapper tokens and partial output are tolerated;
    recovered segments and the raw reply are returned with warnings when needed. The default uses
    only audio, extracting the track from local videos and HTTP(S) video URLs. Temporary oss://
    video URLs are decoded server-side with frames and audio intact; provide an audio URL or local
    file for strictly audio-only processing. Use auto or video
    only when the user explicitly requests synchronized audio-video speaker identification. Those
    modes retain video frames and audio to associate speech with visible people. Oversized local
    media follows the existing OSS or inline fitting paths.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        num_speakers: Expected number of speakers, if known — a hint to guide diarization.
        media_type: Audio (default) extracts the audio track, except for temporary OSS videos
            that require server-side video decoding. Explicit auto retains video and audio
            for video inputs; video also forces video handling for extensionless remote URLs. Use
            auto/video only when the user explicitly requests audio-video speaker identification.
        fps: Video sampling fps, above 0 and up to 15 (default 1.0).
        max_pixels: Per-frame pixel budget (default 200704 ≈ 448²).
        format: Primary output: 'json' (default) or 'srt'.
        language: Spoken-language hint (zh, en, ja, …). Auto-detected if omitted.
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.8-omni-flash.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    n = arguments.get("num_speakers")
    speakers_hint = f" The expected number of speakers is {n}." if n else ""
    prompt = MULTI_SPEAKER_PROMPT + speakers_hint + language_hint(arguments)
    media_type = arguments.get("media_type", "audio")
    if media_type not in {"auto", "audio", "video"}:
        return text_error("media_type must be auto, audio, or video")
    text, blocks = run_omni(arguments, prompt=prompt, mode=media_type, json_output=False)
    if blocks is not None:
        return blocks

    warnings = []
    try:
        segments = parse_speaker_transcript(text, warnings=warnings)
    except ValueError as exc:
        return [summary_block(text), *output_warnings([f"Could not parse speaker transcript: {exc}"])]
    speakers = sorted({str(s["speaker"]) for s in segments if s.get("speaker")})

    result = {"speakers": speakers, "segments": segments}
    out: list[dict[str, Any]] = [json_block(result)]
    if segments:
        srt = segments_to_srt(segments, label_key="speaker")
        if arguments.get("format") == "srt" and srt:
            out = [{"type": "text", "text": srt}, json_block(result)]
        elif srt:
            out.append({"type": "text", "text": srt})
    else:
        out.append(summary_block("(no speech detected)"))
    return out + output_warnings(warnings, raw_text=text if warnings else None)
