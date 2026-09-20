"""Validate a movie-commentary plan against durable evidence and source facts."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.content import text, text_error

from ..contracts import validate_plan


class ValidatePlanArgs(BaseModel):
    project_dir: str
    plan_path: str | None = None
    require_evidence: bool = True


TOOL = {"name": "validate_movie_commentary_plan", "args": ValidatePlanArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate contiguous segment IDs, verbatim narration script parity, evidence paths, source-cut ceiling, and audio decisions before movie-commentary rendering.

    Args:
        project_dir: Movie-commentary project root.
        plan_path: Plan JSON; defaults to plan/editing_plan.json.
        require_evidence: Require every referenced watch note to exist.
    """
    try:
        return [text(json.dumps(validate_plan(**arguments), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
