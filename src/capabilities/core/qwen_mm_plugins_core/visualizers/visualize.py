"""MCP tool: visualize any supported file by extension dispatch."""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_core.renderers import (
    DEFAULT_MAX_PAGES,
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    VIDEO_EXTENSIONS,
    get_renderer,
)
from shared.content import require_file, text_error

_SORTED_EXTS = sorted(SUPPORTED_EXTENSIONS)


class VisualizeArgs(BaseModel):
    file_path: str = Field(description="Absolute path to the file to visualize.")
    pages: Optional[str] = Field(
        default=None,
        description=(
            "Page range for multi-page documents (e.g. '1-5', '3', '1,3,5-8'). 1-based. Default: first 20 pages."
        ),
    )
    budget: Literal["small", "normal", "large"] = Field(
        default="large",
        description=("Resolution preset per page/image: small (~512x512), normal (~1024x1024), large (~1448x1448)."),
    )
    max_pages: int = Field(
        default=20,
        description="Maximum number of pages to render (default 20).",
    )


TOOL: dict[str, Any] = {
    "name": "visualize",
    "description": (
        "Visualize any supported file for model consumption. "
        "Renders documents (PDF, DOCX, PPTX, XLSX, CSV), "
        "code files (syntax-highlighted), SVG, DrawIO diagrams, "
        "subtitles (SRT/VTT), images, and videos as visual output. "
        "Automatically detects file type and applies the appropriate renderer."
    ),
    "args": VisualizeArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    file_path = arguments.get("file_path", "")

    # URLs go to the web renderer.
    if file_path.startswith(("http://", "https://")):
        from qwen_mm_plugins_core.renderers.web import render as web_render

        return web_render(
            file_path,
            pages=arguments.get("pages"),
            max_pages=arguments.get("max_pages", DEFAULT_MAX_PAGES),
            budget=arguments.get("budget", "large"),
        )

    if err := require_file(file_path):
        return err

    ext = os.path.splitext(file_path)[1].lower()
    if not ext:
        return text_error(f"cannot determine file type (no extension): {file_path}")

    if ext not in SUPPORTED_EXTENSIONS:
        return text_error(f"unsupported file type '{ext}'. Supported: {', '.join(_SORTED_EXTS)}")

    if ext in IMAGE_EXTENSIONS:
        from qwen_mm_plugins_core.readers.image import handle as image_handle

        return image_handle(
            {
                "image_path": file_path,
                "budget": arguments.get("budget", "large"),
            }
        )

    if ext in VIDEO_EXTENSIONS:
        from qwen_mm_plugins_core.readers.video import handle as video_handle

        return video_handle(
            {
                "video_path": file_path,
                "budget": arguments.get("budget", "large"),
            }
        )

    renderer = get_renderer(ext)
    if renderer is None:
        return text_error(f"no renderer available for '{ext}'")

    try:
        result = renderer(
            file_path,
            pages=arguments.get("pages"),
            max_pages=arguments.get("max_pages", DEFAULT_MAX_PAGES),
            budget=arguments.get("budget", "large"),
        )
    except Exception as e:
        return [{"type": "text", "text": f"Error rendering {ext} file: {e}"}]

    if not result:
        return [{"type": "text", "text": f"Warning: renderer returned no output for {file_path}"}]

    return result
