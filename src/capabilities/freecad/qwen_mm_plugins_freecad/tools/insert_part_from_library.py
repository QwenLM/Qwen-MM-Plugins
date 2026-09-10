"""MCP tool: insert a part from the FreeCAD parts library addon."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class InsertPartFromLibraryArgs(BaseModel):
    relative_path: str


TOOL = {"name": "insert_part_from_library", "args": InsertPartFromLibraryArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Insert a part from the parts library addon. Returns a message indicating the success or failure
    of the part insertion and a screenshot of the object.

    Args:
        relative_path: The relative path of the part to insert.
    """
    from qwen_mm_plugins_freecad._responses import add_screenshot_if_available, text_response
    from qwen_mm_plugins_freecad.loader import get_connection, only_text_feedback

    relative_path = arguments.get("relative_path", "")
    only_text = only_text_feedback()
    try:
        conn = get_connection()
        res = conn.insert_part_from_library(relative_path)
        if res["success"]:
            response = text_response(f"Part inserted from library: {res['message']}")
        else:
            return text_response(f"Failed to insert part from library: {res['error']}")
        screenshot = None if only_text else conn.get_active_screenshot()
        return add_screenshot_if_available(response, screenshot, only_text)
    except Exception as e:
        return text_response(f"Failed to insert part from library: {e}")
