"""MCP tool: download and import a Sketchfab model by its UID (text return)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DownloadSketchfabModelArgs(BaseModel):
    uid: str
    target_size: float


TOOL = {"name": "download_sketchfab_model", "args": DownloadSketchfabModelArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Download and import a Sketchfab model by its UID. The model will be scaled so its largest
    dimension equals target_size.

    Returns a message with import details including object names, dimensions, and bounding box. The
    model must be downloadable and you must have proper access rights.

    Args:
        uid: The unique identifier of the Sketchfab model.
        target_size: REQUIRED. The target size in Blender units/meters for the largest dimension.
            You must specify the desired size for the model. Examples: Chair=1.0 (1 meter tall),
            Table=0.75 (75cm tall), Car=4.5 (4.5 meters long), Person=1.7 (1.7 meters tall), Small
            object (cup, phone)=0.1 to 0.3.
    """
    from qwen_mm_plugins_blender.loader import get_connection

    uid = arguments.get("uid", "")
    target_size = arguments.get("target_size")
    try:
        result = get_connection().send_command(
            "download_sketchfab_model",
            {
                "uid": uid,
                "normalize_size": True,  # Always normalize
                "target_size": target_size,
            },
        )

        if result is None:
            return [{"type": "text", "text": "Error: Received no response from Sketchfab download request"}]

        if "error" in result:
            return [{"type": "text", "text": f"Error: {result['error']}"}]

        if result.get("success"):
            imported_objects = result.get("imported_objects", [])
            object_names = ", ".join(imported_objects) if imported_objects else "none"

            output = "Successfully imported model.\n"
            output += f"Created objects: {object_names}\n"

            # Add dimension info if available
            if result.get("dimensions"):
                dims = result["dimensions"]
                output += f"Dimensions (X, Y, Z): {dims[0]:.3f} x {dims[1]:.3f} x {dims[2]:.3f} meters\n"

            # Add bounding box info if available
            if result.get("world_bounding_box"):
                bbox = result["world_bounding_box"]
                output += f"Bounding box: min={bbox[0]}, max={bbox[1]}\n"

            # Add normalization info if applied
            if result.get("normalized"):
                scale = result.get("scale_applied", 1.0)
                output += f"Size normalized: scale factor {scale:.6f} applied (target size: {target_size}m)\n"

            return [{"type": "text", "text": output}]
        else:
            return [{"type": "text", "text": f"Failed to download model: {result.get('message', 'Unknown error')}"}]
    except Exception as e:
        return [{"type": "text", "text": f"Error downloading Sketchfab model: {e}"}]
