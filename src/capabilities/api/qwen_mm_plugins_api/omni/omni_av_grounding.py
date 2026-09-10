"""Omni A/V Grounding — locate the time segment(s) matching a text query (temporal localization)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from ._common import json_block, normalize_times, run_omni, summary_block


class OmniAvGroundingArgs(BaseModel):
    file_path: str
    query: str
    top_k: Optional[int] = None
    fps: Optional[float] = None
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
    This is temporal (WHEN) localization — for spatial (WHERE in a frame) use core's grounding tool.
    A local file is uploaded inline, where the endpoint caps a media item at 10 MB of base64, so it
    is transcoded to fit — about 9 min at the default 1 fps / 448² sampling. A longer local video is
    delivered another way automatically: uploaded to OSS when OSS_* is configured (no size limit),
    else split into sampled frames plus its full audio track — at which point the frame spacing
    bounds visual timestamp precision (the audio timeline stays continuous). Passing an http(s)/OSS
    URL keeps full sampling (fetched server-side), as does grounding within a trimmed clip.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        query: What to locate, in natural language (e.g. 'the goal celebration', 'when the speaker
            mentions pricing').
        top_k: Max number of matching segments to return (default: all found).
        fps: Video sampling fps (default 1.0).
        max_pixels: Per-frame pixel budget (default 200704 ≈ 448²).
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.5-omni-plus.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    query = arguments.get("query", "")
    top_k = arguments.get("top_k")
    topk_hint = f" Return at most {top_k} best matches." if top_k else ""
    data, blocks = run_omni(arguments, prompt=_PROMPT.format(query=query, topk=topk_hint), mode="auto")
    if blocks is not None:
        return blocks

    if isinstance(data, list):
        matches = data
    elif isinstance(data, dict):
        matches = data.get("matches") or data.get("segments") or data.get("results") or []
    else:
        matches = []
    matches = normalize_times([m for m in matches if isinstance(m, dict)])
    if top_k:
        matches = matches[:top_k]

    result = {"query": query, "matches": matches}
    summary = f"{len(matches)} matching segment(s) for {query!r}." if matches else f"No segment matched {query!r}."
    return [json_block(result), summary_block(summary)]
