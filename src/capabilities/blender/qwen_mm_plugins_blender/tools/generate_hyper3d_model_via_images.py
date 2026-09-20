"""MCP tool: generate a 3D asset from input images via Hyper3D Rodin and import it into Blender."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


def _valid_url(u: Any) -> bool:
    """A real http(s) URL check. (Plain urlparse() is truthy for any string, so `all(urlparse(i))`
    never rejects anything — it must test scheme + netloc.)"""
    from urllib.parse import urlparse

    if not isinstance(u, str):
        return False
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc)


def _process_bbox(original_bbox: "list[float] | list[int] | None") -> "list[int] | None":
    if original_bbox is None:
        return None
    if all(isinstance(i, int) for i in original_bbox):
        return original_bbox
    if any(i <= 0 for i in original_bbox):
        raise ValueError("Incorrect number range: bbox must be bigger than zero!")
    return [int(float(i) / max(original_bbox) * 100) for i in original_bbox] if original_bbox else None


class GenerateHyper3DViaImagesArgs(BaseModel):
    input_image_paths: Optional[list[str]] = None
    input_image_urls: Optional[list[str]] = None
    bbox_condition: Optional[list[float]] = None


TOOL = {"name": "generate_hyper3d_model_via_images", "args": GenerateHyper3DViaImagesArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate 3D asset using Hyper3D by giving images of the wanted asset, and import the generated
    asset into Blender. The 3D asset has built-in materials. The generated model has a normalized
    size, so re-scaling after generation can be useful. Only one of {input_image_paths,
    input_image_urls} should be given at a time, depending on the Hyper3D Rodin's current mode.

    Args:
        input_image_paths: The **absolute** paths of input images. Even if only one image is
            provided, wrap it into a list. Required if Hyper3D Rodin in MAIN_SITE mode.
        input_image_urls: The URLs of input images. Even if only one image is provided, wrap it into
            a list. Required if Hyper3D Rodin in FAL_AI mode.
        bbox_condition: Optional. If given, it has to be a list of floats of length 3. Controls the
            ratio between [Length, Width, Height] of the model.
    """
    import base64
    import json
    import os
    from pathlib import Path

    from qwen_mm_plugins_blender.loader import get_connection

    input_image_paths = arguments.get("input_image_paths", None)
    input_image_urls = arguments.get("input_image_urls", None)
    bbox_condition = arguments.get("bbox_condition", None)

    if input_image_paths is not None and input_image_urls is not None:
        return [{"type": "text", "text": "Error: Conflict parameters given!"}]
    if input_image_paths is None and input_image_urls is None:
        return [{"type": "text", "text": "Error: No image given!"}]
    if input_image_paths is not None:
        if not all(os.path.exists(i) for i in input_image_paths):
            return [{"type": "text", "text": "Error: not all image paths are valid!"}]
        images = []
        for path in input_image_paths:
            with open(path, "rb") as f:
                images.append((Path(path).suffix, base64.b64encode(f.read()).decode("ascii")))
    elif input_image_urls is not None:
        if not all(_valid_url(i) for i in input_image_urls):
            return [{"type": "text", "text": "Error: not all image URLs are valid!"}]
        images = input_image_urls.copy()
    try:
        result = get_connection().send_command(
            "create_rodin_job",
            {
                "text_prompt": None,
                "images": images,
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
