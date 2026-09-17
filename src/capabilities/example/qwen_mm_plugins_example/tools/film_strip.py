"""Example tool (VIDEO-like return): N synthetic 'frames' as image blocks."""

from __future__ import annotations

import io
from typing import Any

from pydantic import BaseModel

from shared.content import image, require_dep, text


class FilmStripArgs(BaseModel):
    frames: int = 3
    size: int = 96


TOOL = {"name": "make_film_strip", "args": FilmStripArgs}

_COLORS = ["#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6", "#eab308"]


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate N synthetic video frames and return them as image blocks (plus a text summary).
    Demonstrates the multi-frame return shape real video tools use.

    Args:
        frames: Number of frames to generate (1-8).
        size: Frame side length in pixels (32-256).
    """
    if err := require_dep("PIL", "pillow"):
        return err
    from PIL import Image, ImageDraw

    n = max(1, min(int(arguments.get("frames", 3)), 8))
    size = max(32, min(int(arguments.get("size", 96)), 256))

    blocks: list[dict[str, Any]] = [text(f"{n} frames @ {size}x{size}")]
    for i in range(n):
        img = Image.new("RGB", (size, size), _COLORS[i % len(_COLORS)])
        ImageDraw.Draw(img).text((size // 2 - 3, size // 2 - 6), str(i + 1), fill="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        blocks.append(image(buf.getvalue(), "image/png"))
    return blocks
