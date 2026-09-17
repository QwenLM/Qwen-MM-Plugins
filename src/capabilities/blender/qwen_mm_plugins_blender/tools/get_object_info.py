"""MCP tool: get detailed information about a specific object in the Blender scene (JSON text return)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ObjectInfoArgs(BaseModel):
    object_name: str


TOOL = {"name": "get_object_info", "args": ObjectInfoArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get detailed information about a specific object in the Blender scene.

    Args:
        object_name: The name of the object to get information about.
    """
    import json

    from qwen_mm_plugins_blender.loader import get_connection

    object_name = arguments.get("object_name", "")
    try:
        result = get_connection().send_command("get_object_info", {"name": object_name})
        return [{"type": "text", "text": json.dumps(result, indent=2)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error getting object info: {e}"}]
