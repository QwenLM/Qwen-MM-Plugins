"""MCP tool: calibrate_scale — refine metric scale from a target's known real size (pure compute)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class CalibrateScaleArgs(BaseModel):
    scene: Optional[Any] = Field(
        default=None, description="The `scene` object from build_scene (JSON object or JSON string)."
    )
    scene_file: Optional[str] = Field(
        default=None, description="Path to a JSON file holding the scene (alternative to `scene`)."
    )
    target: str = Field(description="Label of the object whose real-world size is known.")
    known_size_m: float = Field(description="The object's known real size in meters (e.g. a chair ~0.5 m wide).")
    dim: str = Field(default="width", description="Which dimension known_size_m refers to: 'width' or 'height'.")
    frame: Optional[int] = Field(
        default=None, description="Frame to measure the observed size in (default: first available)."
    )


TOOL: dict[str, Any] = {
    "name": "calibrate_scale",
    "description": (
        "Refine absolute metric scale from a target's KNOWN real size: returns scale_factor = "
        "known_m / observed_m. Multiply reconstructed depths/distances by it to correct absolute "
        "meters (monocular depth_m alone gets absolute distance wrong). Needs a `scene` from build_scene."
    ),
    "args": CalibrateScaleArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene)
        tool = _scene.new_recon_tool()
        frame = arguments.get("frame")
        result = tool.calibrate_scale(
            recon,
            arguments["target"],
            float(arguments["known_size_m"]),
            dim=arguments.get("dim", "width"),
            frame=int(frame) if frame is not None else None,
        )
    except Exception as e:  # noqa: BLE001
        return text_error(f"calibrate_scale failed: {e}")
    return [json_text(result)]
