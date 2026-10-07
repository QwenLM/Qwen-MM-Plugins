"""Omni A/V descriptions using the official narrative, three-section, and JSON prompts."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from shared.api_omni import is_omni_url
from shared.content import text_error
from shared.omni_media import media_duration

from ._caption_schema import CaptionResult
from ._common import json_block, output_warnings, run_omni, summary_block
from ._prompts import CAPTION_JSON_PROMPT, NARRATIVE_PROMPT, SECTIONS_PROMPT


class OmniAvCaptionArgs(BaseModel):
    file_path: str
    format: Literal["sections", "narrative", "json"] = "sections"
    fps: Optional[float] = Field(default=None, gt=0.0, le=15.0, allow_inf_nan=False)
    max_pixels: Optional[int] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dry_run: bool = False


TOOL = {"name": "omni_av_caption", "args": OmniAvCaptionArgs}

_PROMPTS = {"sections": SECTIONS_PROMPT, "narrative": NARRATIVE_PROMPT, "json": CAPTION_JSON_PROMPT}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe audio/video with an official Qwen-Omni prompt. The default returns three sections:
    chronological storyline, visible text, and speaker-attributed transcription. Choose narrative
    for flowing timestamped paragraphs, or json for scenes and events. Local videos retain
    both frames and audio; oversized media uses temporary OSS, configured OSS, or frames plus audio.
    Model schema/timestamp deviations return the original JSON with a warning, rather than failing
    the call. Timestamps are relative to the source start and may need review.

    Args:
        file_path: Absolute path to a local audio/video file, or an http(s)/OSS URL.
        format: Output style: sections (default), narrative, or json (complete scene/event schema).
        fps: Video sampling fps, above 0 and up to 15 (default 1.0). Raise for rapid actions.
            Higher sampling increases token cost; frame fallback may use fewer frames.
        max_pixels: Per-frame pixel budget (default 200704 ≈ 448²).
        model: Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.8-omni-flash.
        api_key: API key override; otherwise selected by endpoint.
        base_url: OpenAI-compatible base URL override.
        dry_run: Return the request that would be sent, without calling the API.
    """
    output_format = arguments.get("format", "sections")
    if output_format not in _PROMPTS:
        return text_error("format must be sections, narrative, or json")
    data, blocks = run_omni(arguments, prompt=_PROMPTS[output_format], mode="auto", json_output=output_format == "json")
    if blocks is not None:
        return blocks
    if output_format != "json":
        return [summary_block(str(data))]
    warnings = []
    try:
        result = CaptionResult.model_validate(data)
        if not is_omni_url(arguments["file_path"]):
            result.check_duration(media_duration(arguments["file_path"]))
    except ValueError as exc:
        warnings.append(f"Caption JSON differs from the requested schema or timeline: {exc}")
    except Exception:  # noqa: BLE001 — unavailable local metadata must not discard a model reply
        warnings.append("Could not check the local media duration.")
    return [json_block(data), *output_warnings(warnings)]
