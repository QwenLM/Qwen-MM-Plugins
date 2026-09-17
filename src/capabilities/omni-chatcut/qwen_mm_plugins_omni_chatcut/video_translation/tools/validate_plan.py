"""Validate a translated dubbing plan."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.content import text, text_error

from ..contracts import validate_plan


class ValidatePlanArgs(BaseModel):
    project_dir: str
    plan_path: str | None = None


TOOL = {"name": "validate_video_translation_plan", "args": ValidatePlanArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate source/VAD/transcript identity, Agent-authored dubbing groups, timing, speakers, and reference evidence.

    Cross-checks the plan against project.json, the accepted transcript, and the accepted VAD
    evidence: hashes and languages must match, dubbing segments must be ordered and non-overlapping,
    every segment must cite transcript segments of its own speaker, and merged segments need a merge
    reason. Reports whether the plan is valid plus per-segment evidence and timing diagnostics and
    the list of errors; run it before rendering.

    Args:
        project_dir: Existing project directory created by prepare_video_translation_project; project.json
            and the accepted transcript and VAD evidence are read from it.
        plan_path: Plan file to validate. Defaults to plan/translation_plan.json inside the project
            directory.
    """
    try:
        return [text(json.dumps(validate_plan(**arguments), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
