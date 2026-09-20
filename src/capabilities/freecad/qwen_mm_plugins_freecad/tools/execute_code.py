"""MCP tool: execute arbitrary Python inside the running FreeCAD (GUI thread — the safe default)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ExecuteCodeArgs(BaseModel):
    code: str


TOOL = {"name": "execute_code", "args": ExecuteCodeArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute arbitrary Python code in the running FreeCAD, on the GUI thread. This is the safe
    default for FreeCAD automation that touches documents, document objects, FreeCADGui, the active
    view, selection, recompute, or save. Returns the output and (unless disabled) a screenshot of
    the active view.

    Args:
        code: The Python code to execute inside FreeCAD (FreeCAD, Part, etc. are available).
    """
    from qwen_mm_plugins_freecad._responses import add_screenshot_if_available, text_response
    from qwen_mm_plugins_freecad.loader import get_connection, only_text_feedback

    code = arguments.get("code", "")
    only_text = only_text_feedback()
    try:
        conn = get_connection()
        res = conn.execute_code(code)
        if res.get("success"):
            response = text_response(f"Code executed successfully: {res.get('message', '')}")
            screenshot = None if only_text else conn.get_active_screenshot()
            return add_screenshot_if_available(response, screenshot, only_text)
        return text_response(f"Failed to execute code: {res.get('error')}")
    except Exception as e:
        return text_response(f"Failed to execute code: {e}")
