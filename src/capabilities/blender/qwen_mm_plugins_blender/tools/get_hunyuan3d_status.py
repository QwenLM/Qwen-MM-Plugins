"""MCP tool: check whether Hunyuan3D integration is enabled in Blender."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Hunyuan3DStatusArgs(BaseModel):
    pass


TOOL = {"name": "get_hunyuan3d_status", "args": Hunyuan3DStatusArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Check if Hunyuan3D integration is enabled in Blender. Returns a message indicating whether
    Hunyuan3D features are available.
    """
    from qwen_mm_plugins_blender.loader import get_connection

    try:
        result = get_connection().send_command("get_hunyuan3d_status")
        message = result.get("message", "")
        return [{"type": "text", "text": message}]
    except Exception as e:
        return [{"type": "text", "text": f"Error checking Hunyuan3D status: {str(e)}"}]
