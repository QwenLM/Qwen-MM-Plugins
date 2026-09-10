"""MCP tool: crop a region from an image and save the result."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from shared.content import default_output_path, require_dep, require_file, text_error
from shared.image import norm_to_pixel, open_image


class CropArgs(BaseModel):
    image_path: str
    box: list[int] = Field(min_length=4, max_length=4)
    output_path: Optional[str] = None


TOOL = {"name": "crop", "args": CropArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Crop a rectangular region from an image. Saves the cropped result to disk and returns a preview.
    Coordinates are normalized (0-1000), same as grounding output.

    Args:
        image_path: Absolute path to the source image file
        box: Crop region as [x1, y1, x2, y2] in normalized coordinates (0-1000)
        output_path: Where to save the cropped image. Defaults to {stem}_cropped.{ext} next to the
            original.
    """
    image_path = arguments.get("image_path", "")
    if err := require_file(image_path):
        return err
    if err := require_dep("PIL", "pillow"):
        return err

    box = arguments.get("box")
    if not box or len(box) != 4:
        return text_error("'box' must be [x1, y1, x2, y2] (4 integers, 0-1000)")
    try:
        nx1, ny1, nx2, ny2 = (int(v) for v in box)
    except (TypeError, ValueError) as e:
        return text_error(f"invalid box values: {e}")
    if nx1 >= nx2 or ny1 >= ny2:
        return text_error(f"invalid box — x1 must be < x2 and y1 must be < y2, got [{nx1}, {ny1}, {nx2}, {ny2}]")

    from qwen_mm_plugins_core.renderers import labeled_image

    img = open_image(image_path)
    img_w, img_h = img.size
    x1, y1, x2, y2 = norm_to_pixel([nx1, ny1, nx2, ny2], img_w, img_h)
    x1, x2 = max(0, min(x1, img_w)), max(0, min(x2, img_w))
    y1, y2 = max(0, min(y1, img_h)), max(0, min(y2, img_h))
    if x2 <= x1 or y2 <= y1:
        return text_error(
            f"crop region resolves to empty area. "
            f"Image: {img_w}x{img_h}, box: [{nx1},{ny1},{nx2},{ny2}] → pixel [{x1},{y1},{x2},{y2}]"
        )

    cropped = img.crop((x1, y1, x2, y2))
    output_path = arguments.get("output_path") or default_output_path(image_path, "_cropped")
    from shared.image import save_image

    save_image(cropped, output_path)

    summary = (
        f"Cropped: {image_path}\n"
        f"Box: [{nx1},{ny1},{nx2},{ny2}] → pixel [{x1},{y1},{x2},{y2}]\n"
        f"Saved to: {output_path} | {cropped.height}x{cropped.width} (HxW)"
    )
    return [{"type": "text", "text": summary}, *labeled_image("[Preview]", cropped, "normal")]
