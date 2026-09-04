"""MCP tool: view_reason — object-centric viewpoint reasoning (from X's perspective / visibility)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class ViewReasonArgs(BaseModel):
    scene: Optional[Any] = Field(default=None, description="The `scene` object from build_scene.")
    scene_file: Optional[str] = Field(default=None, description="Path to a JSON file holding the scene.")
    op: str = Field(default="scene_layout", description="from_viewpoint | visible_from | scene_layout | line_of_sight")
    frame: int = Field(description="Frame index to reason in.")
    viewpoint: str = Field(description="The object whose perspective to reason from (e.g. 'the person').")
    target: Optional[str] = Field(default=None, description="Target object (visible_from / line_of_sight).")
    targets: Optional[list[str]] = Field(default=None, description="Targets to describe (from_viewpoint).")
    model: Optional[str] = Field(default=None, description="Override the VLM model (default: from env).")


TOOL: dict[str, Any] = {
    "name": "view_reason",
    "description": (
        "Reason from an OBJECT's viewpoint (not the camera): 'scene_layout' (all objects from viewpoint's "
        "perspective), 'from_viewpoint' (describe targets relative to viewpoint), 'visible_from' (is target "
        "visible from viewpoint + direction/distance), 'line_of_sight' (is the view clear or occluded). "
        "Object-perspective mirrors left/right vs the camera. Needs a `scene`."
    ),
    "args": ViewReasonArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from qwen_mm_plugins_video_spatio.experts.view_expert import ViewExpert
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene, with_images=True)
        images = getattr(recon, "_input_images", None)
        ve = ViewExpert()
        ve.set_vlm_module(VLMShim(model=arguments.get("model")))
        op = arguments.get("op", "scene_layout")
        frame = int(arguments["frame"])
        vp = arguments["viewpoint"]
        if op == "from_viewpoint":
            result = ve.from_viewpoint(recon, frame, vp, arguments.get("targets") or [], images=images)
        elif op == "visible_from":
            result = ve.visible_from(recon, frame, vp, arguments["target"], images=images)
        elif op == "line_of_sight":
            result = ve.line_of_sight(recon, frame, vp, arguments["target"], images=images)
        else:
            result = ve.scene_layout(recon, frame, vp, images=images)
    except Exception as e:  # noqa: BLE001
        return text_error(f"view_reason failed: {e}")
    return [json_text(result)]
