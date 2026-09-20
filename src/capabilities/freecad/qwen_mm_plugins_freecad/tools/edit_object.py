"""MCP tool: edit an object in the running FreeCAD (text + optional screenshot)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EditObjectArgs(BaseModel):
    doc_name: str
    obj_name: str
    obj_properties: dict


TOOL = {"name": "edit_object", "args": EditObjectArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Edit an object in FreeCAD.

    This tool is used when the `create_object` tool cannot handle the object creation.

    Args:
        doc_name: The name of the document to edit the object in.
        obj_name: The name of the object to edit.
        obj_properties: The properties of the object to edit.
    """
    from qwen_mm_plugins_freecad._responses import add_screenshot_if_available, text_response
    from qwen_mm_plugins_freecad.loader import get_connection, only_text_feedback

    doc_name = arguments.get("doc_name")
    obj_name = arguments.get("obj_name")
    obj_properties = arguments.get("obj_properties")
    only_text = only_text_feedback()
    try:
        conn = get_connection()
        res = conn.edit_object(doc_name, obj_name, {"Properties": obj_properties})
        if res.get("success"):
            response = text_response(f"Object '{res['object_name']}' edited successfully")
        else:
            return text_response(f"Failed to edit object: {res.get('error')}")
        screenshot = None if only_text else conn.get_active_screenshot()
        return add_screenshot_if_available(response, screenshot, only_text)
    except Exception as e:
        return text_response(f"Failed to edit object: {e}")
