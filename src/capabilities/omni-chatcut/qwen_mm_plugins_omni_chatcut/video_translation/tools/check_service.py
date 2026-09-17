"""Check external dubbing-service readiness."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.content import text, text_error

from ..client import health


class CheckServiceArgs(BaseModel):
    server: str | None = None


TOOL = {"name": "check_dubbing_service", "args": CheckServiceArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Check whether the configured external IndexTTS2 dubbing service is ready without exposing its URL.

    Args:
        server: Optional service URL override.
    """
    try:
        return [text(json.dumps(health(arguments.get("server")), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
