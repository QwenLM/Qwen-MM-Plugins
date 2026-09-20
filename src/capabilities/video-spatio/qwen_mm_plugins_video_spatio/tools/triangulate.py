"""MCP tool: triangulate — 2-view triangulation of a target's world position (pure compute)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class TriangulateArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    target: str
    frame_a: int
    frame_b: int


TOOL: dict[str, Any] = {"name": "triangulate", "args": TriangulateArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Two-view triangulation of `target`'s world position from frames a and b — more accurate than
    monocular depth_m WHEN the camera moved between them. Returns world_xz + triangulation_angle_deg +
    reliable; if angle < 8° the baseline is too small — fall back to depth_m (Pattern A). Needs a
    `scene` from build_scene.

    Args:
        scene: The `scene` object from build_scene (JSON object or JSON string).
        scene_file: Path to a JSON file holding the scene (alternative to `scene`).
        target: Label of the object to triangulate (must be present in both frames).
        frame_a: First frame index.
        frame_b: Second frame index (needs real camera baseline vs frame_a).
    """
    try:
        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene)
        tool = _scene.new_recon_tool()
        result = tool.triangulate(recon, arguments["target"], int(arguments["frame_a"]), int(arguments["frame_b"]))
    except Exception as e:  # noqa: BLE001
        return text_error(f"triangulate failed: {e}")
    return [json_text(result)]
