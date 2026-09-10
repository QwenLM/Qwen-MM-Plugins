"""MCP tool: generate a 3D asset from a text prompt via Hyper3D Rodin and import it into Blender."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


def _process_bbox(original_bbox: "list[float] | list[int] | None") -> "list[int] | None":
    if original_bbox is None:
        return None
    if all(isinstance(i, int) for i in original_bbox):
        return original_bbox
    if any(i <= 0 for i in original_bbox):
        raise ValueError("Incorrect number range: bbox must be bigger than zero!")
    return [int(float(i) / max(original_bbox) * 100) for i in original_bbox] if original_bbox else None


class GenerateHyper3DViaTextArgs(BaseModel):
    text_prompt: str
    bbox_condition: Optional[list[float]] = None


TOOL = {"name": "generate_hyper3d_model_via_text", "args": GenerateHyper3DViaTextArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate 3D asset using Hyper3D by giving description of the desired asset, and import the asset
    into Blender. The 3D asset has built-in materials. The generated model has a normalized size, so
    re-scaling after generation can be useful.

    Args:
        text_prompt: A short description of the desired model in **English**.
        bbox_condition: Optional. If given, it has to be a list of floats of length 3. Controls the
            ratio between [Length, Width, Height] of the model.
    """
    import json

    from qwen_mm_plugins_blender.loader import get_connection

    text_prompt = arguments.get("text_prompt", "")
    bbox_condition = arguments.get("bbox_condition", None)
    try:
        result = get_connection().send_command(
            "create_rodin_job",
            {
                "text_prompt": text_prompt,
                "images": None,
                "bbox_condition": _process_bbox(bbox_condition),
            },
        )
        succeed = result.get("submit_time", False)
        if succeed:
            return [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "task_uuid": result["uuid"],
                            "subscription_key": result["jobs"]["subscription_key"],
                        }
                    ),
                }
            ]
        else:
            return [{"type": "text", "text": json.dumps(result)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error generating Hyper3D task: {e}"}]
