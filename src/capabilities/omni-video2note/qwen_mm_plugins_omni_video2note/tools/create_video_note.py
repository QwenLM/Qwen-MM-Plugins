"""MCP tool for running the Omni Video2Note pipeline."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from shared.content import text


class CreateVideoNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    output_path: str
    language: str = "auto"
    title: str | None = None
    overwrite: bool = False
    quality_profile: str = "balanced"
    omni_model: str | None = None
    vl_model: str | None = None
    review_model: str | None = None
    font: str | None = None
    bold_font: str | None = None
    no_asr: bool = False
    require_asr: bool = False
    dry_run: bool = False


TOOL = {"name": "omni_video2note_create", "args": CreateVideoNoteArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one local tutorial video into an audited, illustrated PDF.

    Runs audio-video understanding, planning, writing/screenshot selection, and PDF rendering
    synchronously. Returns status=complete with output_path and review feedback, or status=failed
    with an error. Review feedback does not block delivery or trigger an automatic repair loop.

    Args:
        video_path: Path to the local tutorial video. URLs are not accepted.
        output_path: Destination path for the generated PDF.
        language: Language for the generated note; auto follows the video.
        title: Use this exact document title when supplied.
        overwrite: Replace an existing PDF only after successful generation.
        quality_profile: Pipeline quality profile to use.
        omni_model: Override the audio-visual understanding model.
        vl_model: Override the planning and writing model.
        review_model: Override the candidate and PDF review model; defaults to the resolved vl_model.
        font: Path to the regular PDF font file.
        bold_font: Path to the bold PDF font file.
        no_asr: Ignore the video's audio and speech during Omni understanding.
        require_asr: Require an audio track and have the Omni model understand it; no separate ASR
            model is used.
        dry_run: Validate only without creating paths or calling a model.
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
