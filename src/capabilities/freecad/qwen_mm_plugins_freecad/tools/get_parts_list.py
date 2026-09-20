"""MCP tool: list the parts available in the FreeCAD parts library addon."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GetPartsListArgs(BaseModel):
    pass


TOOL = {"name": "get_parts_list", "args": GetPartsListArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get the list of parts in the parts library addon."""
    from qwen_mm_plugins_freecad._responses import json_response, text_response
    from qwen_mm_plugins_freecad.loader import get_connection

    try:
        conn = get_connection()
        parts = conn.get_parts_list()
        if parts:
            return json_response(parts)
        return text_response("No parts found in the parts library. You must add parts_library addon.")
    except Exception as e:
        return text_response(f"Failed to get parts list: {e}")
