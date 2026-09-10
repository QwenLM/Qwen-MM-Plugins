"""Example tool (IMAGE return): generate a solid-color PNG."""

from __future__ import annotations

import io
from typing import Any

from pydantic import BaseModel

from shared.content import image, require_dep, text


class SwatchArgs(BaseModel):
    color: str = "#3b82f6"
    size: int = 128


TOOL = {"name": "make_swatch", "args": SwatchArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate a solid-color square PNG. Demonstrates returning an image content block.

    Args:
        color: Fill color: a hex string like '#3b82f6' or a name like 'red'.
        size: Square side length in pixels (16-512).
    """
    if err := require_dep("PIL", "pillow"):
        return err
    from PIL import Image

    color = arguments.get("color", "#3b82f6")
    size = max(16, min(int(arguments.get("size", 128)), 512))

    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    # shared.content.image() base64-encodes the bytes and builds the image content block for you.
    return [
        text(f"{size}x{size} swatch, color={color}"),
        image(buf.getvalue(), "image/png"),
    ]
