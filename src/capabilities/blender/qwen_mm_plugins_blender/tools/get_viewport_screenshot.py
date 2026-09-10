"""MCP tool: capture a screenshot of the current Blender 3D viewport (image return)."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from pydantic import BaseModel

from shared.content import image


class ViewportScreenshotArgs(BaseModel):
    max_size: int = 1000


TOOL = {"name": "get_viewport_screenshot", "args": ViewportScreenshotArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Capture a screenshot of the current Blender 3D viewport. Use it to visually verify the scene
    BEFORE making changes and AFTER executing code or importing assets.

    Args:
        max_size: Maximum size in pixels for the largest dimension.
    """
    from qwen_mm_plugins_blender.loader import get_connection

    max_size = int(arguments.get("max_size", 1000))
    temp_path = os.path.join(tempfile.gettempdir(), f"blender_screenshot_{os.getpid()}.png")
    try:
        result = get_connection().send_command(
            "get_viewport_screenshot",
            {"max_size": max_size, "filepath": temp_path, "format": "png"},
        )
        if "error" in result:
            raise Exception(result["error"])
        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")
        with open(temp_path, "rb") as f:
            return [image(f.read(), "image/png")]
    except Exception as e:
        return [{"type": "text", "text": f"Screenshot failed: {e}"}]
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
