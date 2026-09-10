"""MCP tool: read and resize an image with model-optimized dynamic resolution."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from shared.env import DEFAULT_BUDGET, IMAGE_BUDGET_TOKENS, IMAGE_MIN_PIXELS
from shared.image import budget_to_pixels, open_image, process_image


class ReadImageArgs(BaseModel):
    image_path: str
    budget: Literal["small", "normal", "large"] = DEFAULT_BUDGET


TOOL = {"name": "read_image", "args": ReadImageArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Read an image with model-optimized dynamic resolution. Automatically resizes to fit the target
    model's patch grid, balancing resolution and detail preservation. Returns the resized image for
    model consumption.

    Args:
        image_path: Absolute path to the image file
        budget: Resolution preset: small (~512×512), normal (~1024×1024), large (~1448×1448).
    """
    from shared.content import image, require_dep, require_file, text

    image_path = arguments.get("image_path", "")
    budget = arguments.get("budget", DEFAULT_BUDGET)

    if err := require_file(image_path):
        return err
    if err := require_dep("PIL", "pillow"):
        return err

    max_pixels = budget_to_pixels(budget, IMAGE_BUDGET_TOKENS)

    img = open_image(image_path)
    orig_w, orig_h = img.size
    _, b64, target_w, target_h, mime = process_image(img, IMAGE_MIN_PIXELS, max_pixels)

    summary = f"Image: {image_path} | {orig_h}x{orig_w} → {target_h}x{target_w} (HxW)"
    return [text(summary), image(b64, mime)]
