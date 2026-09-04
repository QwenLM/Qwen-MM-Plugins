"""MCP tool: object_world_motion — object motion in the world frame, camera ego-motion removed."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class ObjectWorldMotionArgs(BaseModel):
    scene: Optional[Any] = Field(
        default=None, description="The `scene` object from build_scene (JSON object or JSON string)."
    )
    scene_file: Optional[str] = Field(
        default=None, description="Path to a JSON file holding the scene (alternative to `scene`)."
    )
    target: str = Field(description="Label of the object whose world motion to compute.")
    frame_a: Optional[int] = Field(
        default=None, description="Start frame (default: first frame where target is present)."
    )
    frame_b: Optional[int] = Field(default=None, description="End frame (default: last frame where target is present).")


TOOL: dict[str, Any] = {
    "name": "object_world_motion",
    "description": (
        "Motion of `target` in the WORLD frame between two frames, with the camera's own ego-motion "
        "REMOVED — distinguishes 'the object moved' from 'the camera moved'. Returns displacement_m / "
        "direction / is_moving / camera_moved_m. Never infer motion from pixel shift; use this. "
        "Needs a `scene` from build_scene."
    ),
    "args": ObjectWorldMotionArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene)
        tool = _scene.new_recon_tool()
        fa = arguments.get("frame_a")
        fb = arguments.get("frame_b")
        result = tool.object_world_motion(
            recon,
            arguments["target"],
            frame_a=int(fa) if fa is not None else None,
            frame_b=int(fb) if fb is not None else None,
        )
    except Exception as e:  # noqa: BLE001
        return text_error(f"object_world_motion failed: {e}")
    return [json_text(result)]
