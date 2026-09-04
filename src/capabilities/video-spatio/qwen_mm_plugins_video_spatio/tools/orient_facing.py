"""MCP tool: orient_facing — which direction an object faces (holistic VLM read + majority vote)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from shared.content import json_text, text_error


class OrientFacingArgs(BaseModel):
    image: str = Field(
        description="Path to the frame image to read the object's facing from (use the FULL frame, not a crop)."
    )
    target: str = Field(description="The object whose facing direction to determine (e.g. 'the person', 'the car').")
    perspective: str = Field(
        default="camera",
        description="'camera' = facing as YOU see it; 'egocentric' = re-expressed in the object's own frame ('imagine you are X').",
    )
    votes: int = Field(
        default=3,
        description="Self-consistency votes: asks the same neutral question N times and majority-votes to de-bias. Default 3.",
    )
    model: Optional[str] = Field(default=None, description="Override the VLM model (default: from env).")


TOOL: dict[str, Any] = {
    "name": "orient_facing",
    "description": (
        "Determine which direction an object is FACING from a single frame — a holistic, majority-voted "
        "VLM read (no reconstruction). Uses the FULL image and votes over several reworded reads to "
        "de-bias. perspective='egocentric' re-expresses left/right/front/back into the object's own frame."
    ),
    "args": OrientFacingArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from PIL import Image

        from qwen_mm_plugins_video_spatio.experts.orientation_expert import OrientationExpert
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        img = Image.open(arguments["image"]).convert("RGB")
        vlm = VLMShim(model=arguments.get("model"))
        result = OrientationExpert.facing_in_image(
            img,
            arguments["target"],
            vlm,
            perspective=arguments.get("perspective", "camera"),
            votes=int(arguments.get("votes", 3)),
        )
    except Exception as e:  # noqa: BLE001
        return text_error(f"orient_facing failed: {e}")
    return [json_text(result)]
