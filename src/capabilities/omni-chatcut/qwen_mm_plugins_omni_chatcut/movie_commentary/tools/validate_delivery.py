"""Validate shard reports and the final movie-commentary delivery."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.content import text, text_error

from ..contracts import validate_delivery


class ValidateDeliveryArgs(BaseModel):
    project_dir: str
    require_final: bool = True


TOOL = {"name": "validate_movie_commentary_delivery", "args": ValidateDeliveryArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate every frozen shard against its execution report and MP4, then require a non-empty final commentary and an all-true independent QA report.

    Args:
        project_dir: Movie-commentary project root.
        require_final: Require final MP4 and passing final QA.
    """
    try:
        return [text(json.dumps(validate_delivery(**arguments), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
