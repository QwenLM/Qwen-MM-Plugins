"""MCP tool: draw bounding boxes on an image and save the result."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from shared.content import default_output_path, require_dep, require_file, text_error
from shared.image import draw_boxes, norm_to_pixel, open_image


class BBox(BaseModel):
    bbox: list[int] = Field(min_length=4, max_length=4)
    label: Optional[str] = None
    color: Optional[str] = None


class DrawBboxArgs(BaseModel):
    image_path: str
    bboxes: list[BBox]
    output_path: Optional[str] = None


TOOL = {"name": "draw_bbox", "args": DrawBboxArgs}


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int] | None:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Draw bounding boxes on an image. Saves the annotated result to disk and returns a preview.
    Coordinates are normalized (0-1000), same as grounding output.

    Args:
        image_path: Absolute path to the source image file
        bboxes: Boxes to draw. Each item has bbox=[x1, y1, x2, y2] in normalized coordinates
            (0-1000), an optional label above the box, and an optional hex color such as
            '#FF0000'. Omitted colors cycle through the default palette.
        output_path: Where to save the annotated image. Defaults to {stem}_annotated.{ext} next to
            the original.
    """
    image_path = arguments.get("image_path", "")
    if err := require_file(image_path):
        return err
    if err := require_dep("PIL", "pillow"):
        return err

    bboxes = arguments.get("bboxes")
    if not bboxes:
        return text_error("'bboxes' is required and must not be empty")

    from qwen_mm_plugins_core.renderers import labeled_image

    img = open_image(image_path)
    detections = [
        {
            "bbox_pixel": norm_to_pixel([int(v) for v in item["bbox"]], img.width, img.height),
            "label": item.get("label", ""),
            "color": _hex_to_rgb(item["color"]) if item.get("color") else None,
        }
        for item in bboxes
        if item.get("bbox") and len(item["bbox"]) == 4
    ]
    annotated = draw_boxes(img, detections)

    output_path = arguments.get("output_path") or default_output_path(image_path, "_annotated")
    from shared.image import save_image

    save_image(annotated, output_path)

    summary = (
        f"Drew {len(detections)} box(es) on: {image_path}\n"
        f"Saved to: {output_path} | {annotated.height}x{annotated.width} (HxW)"
    )
    return [{"type": "text", "text": summary}, *labeled_image("[Preview]", annotated, "normal")]
