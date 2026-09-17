"""MCP tool: execute arbitrary Python inside the running Blender (the primary modeling tool)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ExecuteBlenderCodeArgs(BaseModel):
    code: str


TOOL = {"name": "execute_blender_code", "args": ExecuteBlenderCodeArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute arbitrary Python code in the running Blender (bpy). This is the PRIMARY tool for
    modeling: create/edit geometry, modifiers, materials, lighting, cameras, and rendering. Make
    sure to do it step-by-step by breaking complex work into smaller chunks.

    Args:
        code: The Python code to execute inside Blender (the `bpy` module is available).
    """
    from qwen_mm_plugins_blender.loader import get_connection

    code = arguments.get("code", "")
    try:
        result = get_connection().send_command("execute_code", {"code": code})
        return [{"type": "text", "text": f"Code executed successfully: {result.get('result', '')}"}]
    except Exception as e:
        return [{"type": "text", "text": f"Error executing code: {e}"}]
