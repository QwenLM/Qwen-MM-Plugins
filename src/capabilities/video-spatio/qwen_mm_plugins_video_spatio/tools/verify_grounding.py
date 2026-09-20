"""MCP tool: verify_grounding — VLM check that a bbox region actually shows the claimed label."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from shared.content import json_text, text_error


class VerifyGroundingArgs(BaseModel):
    image: str
    bbox: list[float]
    label: str
    model: Optional[str] = None


TOOL: dict[str, Any] = {"name": "verify_grounding", "args": VerifyGroundingArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Independently VERIFY a grounding: ask the VLM whether the region in `bbox` really shows `label`. Run
    before finalizing any distance/relation conclusion — a wrong bbox is the #1 cause of a flipped
    spatial answer. Returns a match verdict + confidence.

    Args:
        image: Path to the frame image.
        bbox: [x1,y1,x2,y2] region to verify (0-1000 normalized).
        label: The label the region is claimed to show (e.g. 'chair').
        model: Override the VLM model (default: from env).
    """
    try:
        from PIL import Image

        from qwen_mm_plugins_video_spatio.experts.grounding_verifier import GroundingVerifier
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        img = Image.open(arguments["image"]).convert("RGB")
        gv = GroundingVerifier()
        gv.set_vlm_module(VLMShim(model=arguments.get("model")))
        result = gv.verify_object(img, arguments["bbox"], arguments["label"])
    except Exception as e:  # noqa: BLE001
        return text_error(f"verify_grounding failed: {e}")
    return [json_text(result)]
