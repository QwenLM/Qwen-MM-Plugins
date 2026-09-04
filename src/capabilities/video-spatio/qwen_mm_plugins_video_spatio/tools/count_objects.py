"""MCP tool: count_objects — count instances, per-frame or deduplicated across frames."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class CountObjectsArgs(BaseModel):
    scene: Optional[Any] = Field(
        default=None, description="The `scene` object from build_scene (JSON object or JSON string)."
    )
    scene_file: Optional[str] = Field(default=None, description="Path to a JSON file holding the scene.")
    label: Optional[str] = Field(default=None, description="Only count objects of this label (default: all).")
    frame: Optional[int] = Field(
        default=None,
        description="Count in this single frame. If omitted, counts unique objects across ALL frames (BEV-deduplicated).",
    )
    dup_threshold: float = Field(default=0.5, description="BEV dedup distance (meters) for across-frames counting.")
    model: Optional[str] = Field(default=None, description="Override the VLM model (default: from env).")


TOOL: dict[str, Any] = {
    "name": "count_objects",
    "description": (
        "Count objects from the scene's grounded instances (not a bare VLM guess). With `frame` → count "
        "in that frame; without → count UNIQUE physical objects across all frames with BEV dedup (avoids "
        "double-counting the same object seen in multiple frames). Optionally filter by `label`."
    ),
    "args": CountObjectsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from qwen_mm_plugins_video_spatio.experts.counting_expert import CountingExpert
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene)
        ce = CountingExpert()
        ce.set_vlm_module(VLMShim(model=arguments.get("model")))
        label = arguments.get("label")
        frame = arguments.get("frame")
        if frame is not None:
            result = {
                "count": ce.count(recon, frame=int(frame), label=label),
                "by_label": ce.count_by_label(recon, frame=int(frame)),
            }
        else:
            result = ce.count_across_frames(
                recon, label=label, dup_threshold=float(arguments.get("dup_threshold", 0.5))
            )
    except Exception as e:  # noqa: BLE001
        return text_error(f"count_objects failed: {e}")
    return [json_text(result)]
