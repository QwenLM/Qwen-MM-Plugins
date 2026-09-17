"""MCP tool for running the resumable Omni Video2Note pipeline."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shared.content import text


class CreateVideoNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    output_path: str
    workdir: str | None = None
    language: str = "zh-CN"
    resume: bool = False
    overwrite: bool = False
    quality_profile: str = "balanced"
    max_iterations: int | None = Field(default=None, ge=1, le=8)
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

    The operation is resumable through its persistent workdir and returns a JSON result containing
    an exit_code: 0 the PDF passed every gate, 2 a usable PDF that missed the combined quality gate,
    1 no valid PDF. Inspect or resume an interrupted job with omni_video2note_status.

    Args:
        video_path: Path to the local tutorial video. URLs are not accepted.
        output_path: Destination path for the generated PDF.
        workdir: Persistent job directory; defaults to <output>.work.
        language: Language for the generated note.
        resume: Resume an interrupted job from workdir state.
        overwrite: Allow replacement of existing output or owned job state.
        quality_profile: Pipeline quality profile to use.
        max_iterations: Maximum repair iterations (1-8); defaults by profile.
        omni_model: Override the audio-visual understanding model.
        vl_model: Override the planning, writing, and repair model.
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
            "passed": False,
            "output_path": str(arguments.get("output_path", "")),
            "workdir": str(arguments.get("workdir") or ""),
            "best_iteration": None,
            "best_score": 0.0,
            "iterations": 0,
            "error": str(exc),
            "resumable": False,
        }
    return [text(json.dumps(result, ensure_ascii=False, indent=2, default=str))]
