"""Initialize or inspect a durable video-translation project."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from shared.content import text, text_error

from ..project import prepare_project


class PrepareProjectArgs(BaseModel):
    source_movie: str
    project_dir: str
    source_language: str = "auto"
    target_language: str = "en"
    target: Literal["analysis_only", "translation_only", "full", "resume"] = "full"
    style_brief: str = ""
    resume: bool = False


TOOL = {"name": "prepare_video_translation_project", "args": PrepareProjectArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Probe a source video with ffprobe and create the durable Omni ChatCut video-translation project tree plus project.json; or inspect a compatible project for resume.

    Args:
        source_movie: Readable local source video path.
        project_dir: New empty project directory, or an existing project when resume=true.
        source_language: Spoken language of the source audio. "auto" lets transcription detect it.
        target_language: Language the dialogue is translated and dubbed into.
        target: How far this run should go: analysis_only, translation_only, full, or resume.
        style_brief: User-approved translation and dubbing style.
        resume: Inspect and continue a compatible existing project.
    """
    try:
        return [text(json.dumps(prepare_project(**arguments), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
