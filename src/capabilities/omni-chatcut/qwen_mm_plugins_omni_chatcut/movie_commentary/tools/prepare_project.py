"""Initialize or inspect a durable movie-commentary project."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from shared.content import text, text_error

from ..project import prepare_project


class PrepareProjectArgs(BaseModel):
    source_movie: str
    project_dir: str
    target: Literal["analysis_only", "plan_only", "full", "resume"] = "full"
    language: str = "zh"
    style_brief: str = ""
    resume: bool = False


TOOL = {"name": "prepare_movie_commentary_project", "args": PrepareProjectArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Create the durable Omni ChatCut movie-commentary project tree, ffprobe the source, and write project.json plus initial execution facts; or inspect a compatible project for resume.

    Args:
        source_movie: Readable local source movie path.
        project_dir: New empty project directory, or an existing project when resume=true.
        target: How far this run should go: analysis_only, plan_only, full, or resume.
        language: Commentary language.
        style_brief: User-approved narration and edit style.
        resume: Inspect and continue a compatible existing project.
    """
    try:
        result = prepare_project(**arguments)
        return [text(json.dumps(result, ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
