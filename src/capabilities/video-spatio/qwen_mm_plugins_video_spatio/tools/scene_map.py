"""MCP tool: scene_map — structured scene-level outputs (cognitive map / appearance order /
temporal event localization / frame diff)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class SceneMapArgs(BaseModel):
    scene: Optional[Any] = Field(default=None, description="The `scene` object from build_scene.")
    scene_file: Optional[str] = Field(default=None, description="Path to a JSON file holding the scene.")
    op: str = Field(
        default="cognitive_map", description="cognitive_map | appearance_order | locate_event | diff_frames"
    )
    frame: Optional[int] = Field(default=None, description="Frame index (cognitive_map).")
    grid_size: int = Field(default=10, description="Grid resolution for cognitive_map.")
    labels: Optional[list[str]] = Field(default=None, description="Restrict cognitive_map to these labels.")
    describe: bool = Field(default=False, description="cognitive_map: add a VLM description.")
    description: Optional[str] = Field(default=None, description="Event/action to localize (locate_event).")
    frame_a: Optional[int] = Field(default=None, description="First frame (diff_frames).")
    frame_b: Optional[int] = Field(default=None, description="Second frame (diff_frames).")
    model: Optional[str] = Field(default=None, description="Override the VLM model (default: from env).")


TOOL: dict[str, Any] = {
    "name": "scene_map",
    "description": (
        "Structured scene-level views: 'cognitive_map' (grid layout of objects), 'appearance_order' "
        "(objects by first-appearance frame), 'locate_event' (frames where an event/action occurs — "
        "temporal localization), 'diff_frames' (what appeared/disappeared/moved between two frames). "
        "Needs a `scene`."
    ),
    "args": SceneMapArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from qwen_mm_plugins_video_spatio.experts.scene_expert import SceneExpert
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene, with_images=True)
        images = getattr(recon, "_input_images", None)
        se = SceneExpert()
        se.set_vlm_module(VLMShim(model=arguments.get("model")))
        op = arguments.get("op", "cognitive_map")
        if op == "appearance_order":
            result = se.appearance_order(recon)
        elif op == "locate_event":
            if not arguments.get("description"):
                return text_error("locate_event needs `description`.")
            result = se.locate_event(recon, images, arguments["description"])
        elif op == "diff_frames":
            result = se.diff_frames(recon, int(arguments["frame_a"]), int(arguments["frame_b"]), images=images)
        else:
            frame = arguments.get("frame")
            result = se.build_cognitive_map(
                recon,
                frame=int(frame) if frame is not None else None,
                grid_size=int(arguments.get("grid_size", 10)),
                labels=arguments.get("labels"),
                describe=bool(arguments.get("describe", False)),
            )
    except Exception as e:  # noqa: BLE001
        return text_error(f"scene_map failed: {e}")
    return [json_text(result)]
