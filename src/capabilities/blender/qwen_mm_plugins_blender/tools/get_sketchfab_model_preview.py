"""MCP tool: get a preview thumbnail of a Sketchfab model by its UID (image return)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GetSketchfabModelPreviewArgs(BaseModel):
    uid: str


TOOL = {"name": "get_sketchfab_model_preview", "args": GetSketchfabModelPreviewArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get a preview thumbnail of a Sketchfab model by its UID. Use this to visually confirm a model
    before downloading.

    Returns the model's thumbnail as an Image for visual confirmation.

    Args:
        uid: The unique identifier of the Sketchfab model (obtained from search_sketchfab_models).
    """
    from qwen_mm_plugins_blender.loader import get_connection
    from shared.content import image

    uid = arguments.get("uid", "")
    try:
        result = get_connection().send_command("get_sketchfab_model_preview", {"uid": uid})

        if result is None:
            raise Exception("Received no response from Blender")

        if "error" in result:
            raise Exception(result["error"])

        img_format = result.get("format", "jpeg")
        return [image(result["image_data"], f"image/{img_format}")]
    except Exception as e:
        return [{"type": "text", "text": f"Failed to get preview: {e}"}]
