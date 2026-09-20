"""MCP tool for running the Omni Video2Note pipeline."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shared.content import text


class CreateVideoNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    output_path: str
    language: str = "auto"
    title: str | None = None
    overwrite: bool = False
    quality_profile: str = "fast"
    omni_model: str | None = None
    vl_model: str | None = None
    review_model: str | None = None
    font: str | None = None
    bold_font: str | None = None
    no_asr: bool = False
    require_asr: bool = False
    dry_run: bool = False
    time_budget_seconds: float = Field(default=150.0, ge=1, le=1800, allow_inf_nan=False, strict=True)


TOOL = {"name": "omni_video2note_create", "args": CreateVideoNoteArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one local tutorial video into an illustrated PDF using Omni.

    Runs video understanding, combined note writing, local screenshot selection and PDF rendering
    synchronously. Model errors preserve usable content and return warnings when a PDF can be made.
    There are no model-based screenshot scoring, JSON repair, or final PDF review calls.

    Args:
        video_path: Path to the local tutorial video. URLs are not accepted.
        output_path: Destination path for the generated PDF.
        language: Language for the generated note; auto follows the video.
        title: Use this exact document title when supplied.
        overwrite: Replace an existing PDF only after successful generation.
        quality_profile: Local sampling profile; fast is the default.
        omni_model: Override the Omni model used for every model request.
        vl_model: Deprecated compatibility argument; ignored in favor of omni_model.
        review_model: Deprecated compatibility argument; ignored in favor of omni_model.
        font: Path to the regular PDF font file.
        bold_font: Path to the bold PDF font file.
        no_asr: Ignore the video's audio and speech during Omni understanding.
        require_asr: Require an audio track and have the Omni model understand it; no separate ASR
            model is used.
        dry_run: Validate only without creating paths or calling a model.
        time_budget_seconds: Shared model-request time budget, including retries (default: 150).
            Local media processing and PDF rendering require additional time.
    """
    try:
        args = CreateVideoNoteArgs.model_validate(arguments)
        from ..pipeline.runner import run_video2note

        result = run_video2note(**args.model_dump())
    except Exception as exc:  # validation/runtime errors share the JSON contract; control exceptions propagate
        result = {
            "exit_code": 1,
            "status": "failed",
            "output_path": str(arguments.get("output_path", "")),
            "error": str(exc),
        }

    return [text(json.dumps(result, ensure_ascii=False, indent=2, default=str))]
