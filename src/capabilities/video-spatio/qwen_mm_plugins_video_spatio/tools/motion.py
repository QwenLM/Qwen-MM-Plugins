"""MCP tool: motion — video motion segmentation + trajectory extrapolation (pure compute)."""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field

from shared.content import json_text, text_error


class MotionArgs(BaseModel):
    frames: Optional[list[str]] = Field(
        default=None, description="Paths to frame images, in order — for motion segmentation across the clip."
    )
    motion_threshold: Optional[float] = Field(
        default=None, description="Optional motion threshold for segmentation (default: auto)."
    )
    past_points: Optional[list[list[float]]] = Field(
        default=None,
        description="A past 2D/3D track [[x,y(,z)], ...] — when given, extrapolate future points instead of segmenting.",
    )
    n_future: int = Field(default=3, description="Number of future points to extrapolate (predict mode).")
    order: int = Field(default=2, description="Polynomial fit order for extrapolation (predict mode).")


TOOL: dict[str, Any] = {
    "name": "motion",
    "description": (
        "Two pure-compute modes: (1) motion SEGMENTATION across a clip — pass `frames` to get which "
        "regions/frames contain motion (frame-difference based); (2) trajectory EXTRAPOLATION — pass "
        "`past_points` to fit and predict `n_future` points. Reason about motion in world/metric terms, "
        "not raw pixels."
    ),
    "args": MotionArgs,
}


def _to_jsonable(obj):
    try:
        import numpy as np

        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    return obj


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from qwen_mm_plugins_video_spatio.experts.motion_expert import MotionExpert

        if arguments.get("past_points"):
            fut = MotionExpert.predict_trajectory(
                arguments["past_points"],
                n_future=int(arguments.get("n_future", 3)),
                order=int(arguments.get("order", 2)),
            )
            return [
                {
                    "type": "text",
                    "text": json.dumps({"future_points": _to_jsonable(fut)}, ensure_ascii=False, default=str),
                }
            ]

        frames = arguments.get("frames")
        if not frames:
            return text_error("provide `frames` (for segmentation) or `past_points` (for extrapolation).")
        from PIL import Image

        imgs = [Image.open(p).convert("RGB") for p in frames]
        result = MotionExpert.segment(imgs, motion_threshold=arguments.get("motion_threshold"))
    except Exception as e:  # noqa: BLE001
        return text_error(f"motion failed: {e}")
    return [json_text(_to_jsonable(result))]
