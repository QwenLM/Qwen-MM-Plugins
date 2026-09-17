"""MCP tool: list all objects in a FreeCAD document (JSON + optional screenshot)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GetObjectsArgs(BaseModel):
    doc_name: str


TOOL = {"name": "get_objects", "args": GetObjectsArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get all objects in a document. You can use this tool to get the objects in a document to see
    what you can check or edit. Returns a list of objects in the document and a screenshot of the
    document.

    Args:
        doc_name: The name of the document to get the objects from.
    """
    from qwen_mm_plugins_freecad._responses import (
        add_screenshot_if_available,
        json_response,
        text_response,
    )
    from qwen_mm_plugins_freecad.loader import get_connection, only_text_feedback

    doc_name = arguments.get("doc_name", "")
    only_text = only_text_feedback()
    try:
        conn = get_connection()
        response = json_response(conn.get_objects(doc_name))
        screenshot = None if only_text else conn.get_active_screenshot()
        return add_screenshot_if_available(response, screenshot, only_text)
    except Exception as e:
        return text_response(f"Failed to get objects: {e}")
