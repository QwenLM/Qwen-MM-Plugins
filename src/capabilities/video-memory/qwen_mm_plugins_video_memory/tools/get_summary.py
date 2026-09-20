"""MCP tool: get video-level root summary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from . import VideoPath


class GetSummaryArgs(BaseModel):
    video_path: VideoPath = None


TOOL = {"name": "get_summary", "args": GetSummaryArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get the video-level root summary. Returns: title, description, themes, key entities, and
    emotional tone. When to use: You need a quick overview of the entire video before drilling down,
    or the user asks 'What is this video about?'

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
    """
    from qwen_mm_plugins_video_memory.loader import get_toolkit

    args = dict(arguments or {})
    toolkit = get_toolkit(args.pop("video_path", None))
    result = toolkit.get_summary()
    return [{"type": "text", "text": result}]
