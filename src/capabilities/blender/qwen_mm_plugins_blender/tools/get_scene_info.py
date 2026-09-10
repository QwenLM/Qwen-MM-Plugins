"""MCP tool: get detailed information about the current Blender scene (JSON text return)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


class SceneInfoArgs(BaseModel):
    pass


TOOL = {"name": "get_scene_info", "args": SceneInfoArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get detailed information about the current Blender scene (objects, materials, etc.)."""
    from qwen_mm_plugins_blender.loader import get_connection

    try:
        result = get_connection().send_command("get_scene_info")
        return [{"type": "text", "text": json.dumps(result, indent=2)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error getting scene info: {e}"}]
