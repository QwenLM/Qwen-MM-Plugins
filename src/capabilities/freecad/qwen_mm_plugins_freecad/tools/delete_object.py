"""MCP tool: delete an object from the running FreeCAD (text + optional screenshot)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DeleteObjectArgs(BaseModel):
    doc_name: str
    obj_name: str


TOOL = {"name": "delete_object", "args": DeleteObjectArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Delete an object in FreeCAD.

    Args:
        doc_name: The name of the document to delete the object from.
        obj_name: The name of the object to delete.
    """
    from qwen_mm_plugins_freecad._responses import add_screenshot_if_available, text_response
    from qwen_mm_plugins_freecad.loader import get_connection, only_text_feedback

    doc_name = arguments.get("doc_name")
    obj_name = arguments.get("obj_name")
    only_text = only_text_feedback()
    try:
        conn = get_connection()
        res = conn.delete_object(doc_name, obj_name)
        if res.get("success"):
            response = text_response(f"Object '{res['object_name']}' deleted successfully")
        else:
            return text_response(f"Failed to delete object: {res.get('error')}")
        screenshot = None if only_text else conn.get_active_screenshot()
        return add_screenshot_if_available(response, screenshot, only_text)
    except Exception as e:
        return text_response(f"Failed to delete object: {e}")
