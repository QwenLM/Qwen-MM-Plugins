"""MCP tool: triangulate — 2-view triangulation of a target's world position (pure compute)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class TriangulateArgs(BaseModel):
    scene: Optional[Any] = Field(
        default=None, description="The `scene` object from build_scene (JSON object or JSON string)."
    )
    scene_file: Optional[str] = Field(
        default=None, description="Path to a JSON file holding the scene (alternative to `scene`)."
    )
    target: str = Field(description="Label of the object to triangulate (must be present in both frames).")
    frame_a: int = Field(description="First frame index.")
    frame_b: int = Field(description="Second frame index (needs real camera baseline vs frame_a).")


TOOL: dict[str, Any] = {
    "name": "triangulate",
    "description": (
        "Two-view triangulation of `target`'s world position from frames a and b — more accurate "
        "than monocular depth_m WHEN the camera moved between them. Returns world_xz + "
        "triangulation_angle_deg + reliable; if angle < 8° the baseline is too small — fall back to "
        "depth_m (Pattern A). Needs a `scene` from build_scene."
    ),
    "args": TriangulateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene)
        tool = _scene.new_recon_tool()
        result = tool.triangulate(recon, arguments["target"], int(arguments["frame_a"]), int(arguments["frame_b"]))
    except Exception as e:  # noqa: BLE001
        return text_error(f"triangulate failed: {e}")
    return [json_text(result)]
