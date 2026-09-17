"""MCP tool: check if Hyper3D Rodin integration is enabled in Blender."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Hyper3DStatusArgs(BaseModel):
    pass


TOOL = {"name": "get_hyper3d_status", "args": Hyper3DStatusArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Check if Hyper3D Rodin integration is enabled in Blender. Returns a message indicating whether
    Hyper3D Rodin features are available.
    """
    from qwen_mm_plugins_blender.loader import get_connection

    try:
        result = get_connection().send_command("get_hyper3d_status")
        message = result.get("message", "")
        return [{"type": "text", "text": message}]
    except Exception as e:
        return [{"type": "text", "text": f"Error checking Hyper3D status: {e}"}]
