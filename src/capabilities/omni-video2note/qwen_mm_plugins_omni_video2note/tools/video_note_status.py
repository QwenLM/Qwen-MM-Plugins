"""MCP tool for reading Omni Video2Note job status without network access."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from shared.content import text


class VideoNoteStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workdir: str | None = None
    output_path: str | None = None


TOOL = {"name": "omni_video2note_status", "args": VideoNoteStatusArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Read a Video2Note job's local status and resumability information.

    Inspection is local: it reads the job directory and calls no model API. Pass either argument.

    Args:
        workdir: Job directory; omit when output_path is supplied.
        output_path: Output PDF used to derive the default <output>.work path.
    """
    try:
        args = VideoNoteStatusArgs.model_validate(arguments)
        from ..pipeline.runner import get_status

        result = get_status(workdir=args.workdir, output_path=args.output_path)
    except Exception as exc:  # validation/runtime errors share the JSON contract; control exceptions propagate
        result = {
            "exit_code": 1,
            "status": "failed",
            "passed": False,
            "workdir": str(arguments.get("workdir") or ""),
            "resumable": False,
            "final_pdf": None,
            "best_iteration": None,
            "best_score": 0.0,
            "iterations": 0,
            "error": str(exc),
        }
    return [text(json.dumps(result, ensure_ascii=False, indent=2, default=str))]
