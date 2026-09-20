"""MCP tool: close and re-open a FreeCAD document to pick up external file changes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ReloadDocumentArgs(BaseModel):
    doc_name: str


TOOL = {"name": "reload_document", "args": ReloadDocumentArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Close and re-open a document to pick up external file changes. Use this AFTER the document's
    .FCStd file has been modified by something outside of FreeCAD's GUI process — for example, a
    headless `freecadcmd` script that edited and saved the file. The open GUI document is otherwise
    unaware of on-disk changes; this tool closes the stale in-memory copy and reopens the file from
    disk so the GUI shows current geometry.

    Args:
        doc_name: The name of the open document to reload. Must match the name shown by
            list_documents.
    """
    from qwen_mm_plugins_freecad._responses import text_response
    from qwen_mm_plugins_freecad.loader import get_connection

    doc_name = arguments.get("doc_name", "")
    try:
        conn = get_connection()
        res = conn.reload_document(doc_name)
        if res.get("success"):
            return text_response(f"Document '{res['document_name']}' reloaded from disk.")
        return text_response(f"Failed to reload document: {res.get('error')}")
    except Exception as e:
        return text_response(f"Failed to reload document: {e}")
