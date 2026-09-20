"""MCP tool: create a new document in the running FreeCAD."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CreateDocumentArgs(BaseModel):
    name: str


TOOL = {"name": "create_document", "args": CreateDocumentArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Create a new document in FreeCAD. Returns a message indicating the success or failure of the
    document creation.

    Args:
        name: The name of the document to create.
    """
    from qwen_mm_plugins_freecad._responses import text_response
    from qwen_mm_plugins_freecad.loader import get_connection

    name = arguments.get("name", "")
    try:
        conn = get_connection()
        res = conn.create_document(name)
        if res["success"]:
            return text_response(f"Document '{res['document_name']}' created successfully")
        return text_response(f"Failed to create document: {res['error']}")
    except Exception as e:
        return text_response(f"Failed to create document: {e}")
