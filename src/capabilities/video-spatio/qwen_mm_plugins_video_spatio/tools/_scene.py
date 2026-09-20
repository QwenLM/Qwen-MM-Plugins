"""Shared helpers: (de)serialize the `scene` JSON exchanged between video-spatio tools.

`build_scene` emits a `scene` (JSON) capturing instance-level perception + camera poses.
Every downstream geometry tool (triangulate / object_world_motion / calibrate_scale /
render_scene_views / visualize_bev) takes that `scene` back and rebuilds a lightweight
``Reconstruction`` (kernel_types) so it can reuse the EXACT geometry methods from
``ReconstructPromptTool`` — no math is re-implemented here.
"""

from __future__ import annotations

import json
from typing import Any

SCENE_SCHEMA = "video-spatio/scene@1"


def load_scene(arguments: dict[str, Any]) -> dict:
    """Resolve the `scene` argument: accept a dict, a JSON string, or a `scene_file` path."""
    scene = arguments.get("scene")
    if scene is None and arguments.get("scene_file"):
        with open(arguments["scene_file"], "r", encoding="utf-8") as f:
            scene = json.load(f)
    if isinstance(scene, str):
        scene = json.loads(scene)
    if not isinstance(scene, dict):
        raise ValueError("provide `scene` (the JSON object from build_scene) or `scene_file`")
    if scene.get("type") != SCENE_SCHEMA:
        raise ValueError(f"expected scene type {SCENE_SCHEMA!r}")
    return scene


def scene_to_recon(scene: dict, with_images: bool = False):
    """Rebuild a `Reconstruction` from a scene dict.

    Geometry-only tools (triangulate / object_world_motion / calibrate_scale) use just
    instances/cameras/frame_indices/metric_scale → with_images=False. Render tools
    (render_scene_views / bev overlays) need the RGB frames → with_images=True loads them.
    """
    from qwen_mm_plugins_video_spatio.scene_types import Reconstruction

    frame_indices = [int(x) for x in scene.get("frame_indices", [])]
    instances = {int(k): v for k, v in (scene.get("instances") or {}).items()}
    cameras = {int(k): v for k, v in (scene.get("cameras") or {}).items()}

    rgb = None
    input_images = None
    if with_images:
        import numpy as np
        from PIL import Image

        paths = scene.get("frames") or []
        imgs = [Image.open(p).convert("RGB") for p in paths]
        input_images = imgs
        if imgs:
            rgb = np.stack([np.array(im) for im in imgs], axis=0)

    return Reconstruction(
        points=None,
        depth=None,
        intrinsics=None,
        extrinsics=None,
        metric_scale=float(scene.get("metric_scale", 1.0)),
        gravity_direction=[0.0, 1.0, 0.0],
        rgb=rgb,
        frame_indices=frame_indices,
        input_images=input_images,
        is_video=bool(scene.get("is_video", True)),
        instances=instances,
        cameras=cameras,
    )


def new_recon_tool():
    """Instantiate ReconstructPromptTool for its (pure) geometry methods — no VLM/config needed."""
    from qwen_mm_plugins_video_spatio.experts.reconstruct import ReconstructPromptTool

    return ReconstructPromptTool()
