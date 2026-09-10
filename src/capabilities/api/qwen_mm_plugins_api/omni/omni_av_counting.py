"""Omni A/V Counting — count occurrences of a specified event/object/action in audio/video."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from ._common import json_block, normalize_times, run_omni, summary_block


class OmniAvCountingArgs(BaseModel):
    file_path: str
    target: str
    fps: Optional[float] = None
    max_pixels: Optional[int] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dry_run: bool = False


TOOL = {"name": "omni_av_counting", "args": OmniAvCountingArgs}

_PROMPT = (
    'Count how many times the following occurs in this media: "{target}". Watch the frames and listen '
    "to the audio. Report the total count and, for each occurrence, an accurate start and end time in "
    "seconds (use the same value for both if it is instantaneous) plus a short note. "
    "Output STRICTLY this JSON and nothing else: "
    '{{"count": <int>, "occurrences": [{{"start": <sec>, "end": <sec>, "note": "<what happened>"}}]}}'
)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Count how many times a specified event/object/action occurs in an audio/video, returning the
    total count and the timestamp of each occurrence, using the Qwen-Omni model (reads frames +
    audio). A local file is uploaded inline, where the endpoint caps a media item at 10 MB of
    base64, so it is transcoded to fit — about 9 min at the default 1 fps / 448² sampling (less at a
    higher fps). A longer local video is delivered another way automatically: uploaded to OSS when
    OSS_* is configured (no size limit), else split into sampled frames plus its full audio track —
    at which point the frame spacing bounds how finely occurrences can be separated. Passing an
    http(s)/OSS URL keeps full sampling (fetched server-side), as does counting over a trimmed clip.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        target: What to count, in natural language (e.g. 'times a car passes', 'goals scored', 'the
            word yes').
        fps: Video sampling fps (default 1.0). Raise for fast/frequent events — at the cost of a
            larger upload, which shortens the maximum uploadable length.
        max_pixels: Per-frame pixel budget (default 200704 ≈ 448²).
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.5-omni-plus.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    target = arguments.get("target", "")
    data, blocks = run_omni(arguments, prompt=_PROMPT.format(target=target), mode="auto")
    if blocks is not None:
        return blocks

    occurrences: list = []
    count: Optional[int] = None
    if isinstance(data, dict):
        occ = data.get("occurrences") or data.get("results") or []
        occurrences = normalize_times([o for o in occ if isinstance(o, dict)])
        raw_count = data.get("count")
        if isinstance(raw_count, (int, float)):
            count = int(raw_count)
    elif isinstance(data, list):
        occurrences = normalize_times([o for o in data if isinstance(o, dict)])
    if count is None:
        count = len(occurrences)

    result = {"target": target, "count": count, "occurrences": occurrences}
    return [json_block(result), summary_block(f"Counted {count} occurrence(s) of {target!r}.")]
