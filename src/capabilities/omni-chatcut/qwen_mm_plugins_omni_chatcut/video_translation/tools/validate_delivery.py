"""Independently validate a translated-video delivery."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.content import text, text_error

from ..contracts import validate_delivery


class ValidateDeliveryArgs(BaseModel):
    project_dir: str


TOOL = {"name": "validate_video_translation_delivery", "args": ValidateDeliveryArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify the final video hash, streams, duration, full decode, render report, and required QA checks.

    Re-measures the delivered file independently of the render run: the video stream must match the
    source, the audio must have been replaced, the render report and translation plan must still agree
    with what was delivered, and every final QA and agent review check must pass.

    Args:
        project_dir: Existing project directory holding the rendered delivery (full/translated.mp4 plus
            its render report, final QA, and agent review files).
    """
    try:
        return [text(json.dumps(validate_delivery(**arguments), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
