"""MCP tool: scene_map — structured scene-level outputs (cognitive map / appearance order /
temporal event localization / frame diff)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class SceneMapArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    op: str = "cognitive_map"
    frame: Optional[int] = None
    grid_size: int = 10
    labels: Optional[list[str]] = None
    describe: bool = False
    description: Optional[str] = None
    frame_a: Optional[int] = None
    frame_b: Optional[int] = None
    model: Optional[str] = None


TOOL: dict[str, Any] = {"name": "scene_map", "args": SceneMapArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured scene-level views: 'cognitive_map' (grid layout of objects), 'appearance_order' (objects
    by first-appearance frame), 'locate_event' (frames where an event/action occurs — temporal
    localization), 'diff_frames' (what appeared/disappeared/moved between two frames). Needs a `scene`.

    Args:
        scene: The `scene` object from build_scene.
        scene_file: Path to a JSON file holding the scene.
        op: cognitive_map | appearance_order | locate_event | diff_frames
        frame: Frame index (cognitive_map).
        grid_size: Grid resolution for cognitive_map.
        labels: Restrict cognitive_map to these labels.
        describe: cognitive_map: add a VLM description.
        description: Event/action to localize (locate_event).
        frame_a: First frame (diff_frames).
        frame_b: Second frame (diff_frames).
        model: Override the VLM model (default: from env).
    """
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
