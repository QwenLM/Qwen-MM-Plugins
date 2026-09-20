"""MCP tool: select_keyframes — pick the most informative frames for a question (mostly compute)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class SelectKeyframesArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    strategy: str = "motion"
    n: int = 8
    total_frames: Optional[int] = None
    label: Optional[str] = None
    labels: Optional[list[str]] = None
    model: Optional[str] = None


TOOL: dict[str, Any] = {"name": "select_keyframes", "args": SelectKeyframesArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Pick the most informative frame indices: 'uniform' (evenly spaced), 'motion' (most diverse camera
    viewpoints), 'coverage' (frames where a target is best visible), 'covisibility' (frames where
    multiple objects are co-visible). Needs a `scene` except for 'uniform'.

    Args:
        scene: The `scene` object from build_scene (needed for motion/coverage/covisibility).
        scene_file: Path to a JSON file holding the scene.
        strategy: uniform | motion | coverage | covisibility
        n: How many frames to select.
        total_frames: Total frame count (required for 'uniform').
        label: Target label (coverage).
        labels: Target labels (covisibility).
        model: Override the VLM model (default: from env).
    """
    try:
        from qwen_mm_plugins_video_spatio.experts.keyframe_selector import KeyframeSelector
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        strat = arguments.get("strategy", "motion")
        n = int(arguments.get("n", 8))
        if strat == "uniform":
            if arguments.get("total_frames") is None:
                return text_error("'uniform' needs `total_frames`.")
            result = KeyframeSelector.select_uniform(int(arguments["total_frames"]), n=n)
        else:
            scene = _scene.load_scene(arguments)
            recon = _scene.scene_to_recon(scene, with_images=True)
            ks = KeyframeSelector()
            ks.set_vlm_module(VLMShim(model=arguments.get("model")))
            if strat == "coverage":
                result = ks.select_by_coverage(
                    recon, arguments["label"], n=n, images=getattr(recon, "_input_images", None)
                )
            elif strat == "covisibility":
                result = ks.select_by_covisibility(recon, arguments.get("labels") or [], n=n)
            else:
                result = ks.select_by_motion(recon, n=n)
    except Exception as e:  # noqa: BLE001
        return text_error(f"select_keyframes failed: {e}")
    return [json_text({"frames": result})]
