"""Omni A/V temporal grounding — locate time segments matching a text query."""

from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from shared.content import text_error

from ._common import json_block, normalize_times, output_warnings, run_omni, summary_block
from ._prompts import AUDIO_EVENT_PROMPT


class OmniAvGroundingArgs(BaseModel):
    file_path: str
    query: str
    mode: Literal["av", "audio_event"] = "av"
    top_k: Optional[int] = Field(default=None, gt=0)
    fps: Optional[float] = Field(default=None, gt=0.0, le=15.0, allow_inf_nan=False)
    max_pixels: Optional[int] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dry_run: bool = False


TOOL = {"name": "omni_av_grounding", "args": OmniAvGroundingArgs}


_PROMPT = (
    'Find every time segment in this media that matches the query: "{query}". For each match give an '
    "accurate start and end time in seconds, a confidence score in [0,1], and a one-line reason.{topk} "
    "Output STRICTLY this JSON and nothing else: "
    '{{"matches": [{{"start": <sec>, "end": <sec>, "score": <0-1>, "reason": "<why>"}}]}}. '
    'If nothing matches, output {{"matches": []}}.'
)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Temporal grounding: given a text query, locate the time segment(s) in an audio/video where it
    occurs, returning start/end seconds per match, using the Qwen-Omni model (reads frames + audio).
    Use audio_event mode for a specified sound label with the official sound-localization prompt.
    That mode returns type/start/end for each occurrence; score and reason are not manufactured.
    Extra fields, differing labels, and timestamp mistakes are retained with warnings. Numeric and
    clock-string timestamps are converted to seconds when possible.
    Local videos and HTTP(S) video URLs in audio_event mode contribute only their audio track.
    Temporary oss:// video URLs require server-side video decoding with frames and audio intact;
    provide an audio URL or local file for strictly audio-only processing.
    This is temporal grounding (WHEN) — for spatial grounding (WHERE in a frame), use core's tool.
    A local file is uploaded inline, where the endpoint caps a media item at 10 MB of base64, so it
    is transcoded to fit — about 9 min at the default 1 fps / 448² sampling. A longer local video is
    delivered another way automatically: uploaded to OSS when OSS_* is configured (no size limit),
    else split into sampled frames plus its full audio track — at which point the frame spacing
    bounds visual timestamp precision (the audio timeline stays continuous). Passing an http(s)/OSS
    URL keeps full sampling (fetched server-side), as does grounding within a trimmed clip.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        query: What to locate, in natural language (e.g. 'the goal celebration', 'when the speaker
            mentions pricing'), or the exact sound label in audio_event mode (e.g. dog_barking).
        mode: av (default) for general audio-visual queries; audio_event for a specified sound.
        top_k: Max number of matching segments to return (default: all found).
        fps: Video sampling fps, above 0 and up to 15 (default 1.0).
        max_pixels: Per-frame pixel budget (default 200704 ≈ 448²).
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.8-omni-flash.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    query = arguments.get("query", "")
    mode = arguments.get("mode", "av")
    if mode not in {"av", "audio_event"}:
        return text_error("mode must be av or audio_event")
    if mode == "audio_event" and not query.strip():
        return text_error("query must specify a sound event label")
    top_k = arguments.get("top_k")
    topk_hint = f" Return at most {top_k} best matches." if top_k else ""
    prompt = (
        AUDIO_EVENT_PROMPT.replace("[audio event label]", query)
        if mode == "audio_event"
        else _PROMPT.format(query=query, topk=topk_hint)
    )
    data, blocks = run_omni(arguments, prompt=prompt, mode="audio" if mode == "audio_event" else "auto")
    if blocks is not None:
        return blocks

    warnings = []
    if isinstance(data, list):
        matches = data
    elif isinstance(data, dict):
        matches = data.get("matches") or data.get("segments") or data.get("results") or []
        if mode == "audio_event" and any(key in data for key in ("start_time", "start")):
            matches = [data]
            warnings.append("The model returned a single event instead of an array.")
    else:
        matches = []
        warnings.append("The model reply did not contain a recognizable event list.")
    if not isinstance(matches, list):
        matches = [matches]
        warnings.append("The model event list was not an array.")
    if any(not isinstance(match, dict) for match in matches):
        warnings.append("Some events could not be interpreted; the raw reply is retained.")
    if mode == "audio_event":
        matches = [
            {
                **{key: value for key, value in event.items() if key not in {"start_time", "end_time"}},
                **({"start": event["start_time"]} if "start_time" in event else {}),
                **({"end": event["end_time"]} if "end_time" in event else {}),
            }
            for event in matches
            if isinstance(event, dict)
        ]
        if any(event.get("type") != query for event in matches):
            warnings.append("Some event labels differ from the query; model labels are retained.")
    matches = normalize_times([m for m in matches if isinstance(m, dict)])
    if mode == "audio_event" and any(
        not isinstance(m.get("start"), (int, float))
        or not isinstance(m.get("end"), (int, float))
        or not math.isfinite(m["start"])
        or not math.isfinite(m["end"])
        or m["start"] < 0
        or m["end"] < m["start"]
        for m in matches
    ):
        warnings.append("Some event timestamps need review; model values are retained.")
    if top_k:
        matches = matches[:top_k]

    result = {"query": query, "matches": matches}
    if warnings:
        result["raw"] = data
    summary = f"{len(matches)} matching segment(s) for {query!r}." if matches else f"No segment matched {query!r}."
    return [json_block(result), summary_block(summary), *output_warnings(warnings)]
