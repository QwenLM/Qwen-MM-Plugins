"""MCP tool: full subgraph detail for a specific macro event."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from . import VideoPath


class GetSubgraphArgs(BaseModel):
    video_path: VideoPath = None
    macro_id: str


TOOL = {"name": "get_subgraph", "args": GetSubgraphArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get detailed subgraph for a specific Macro Event. THIS IS THE MOST INFORMATION-RICH TOOL.
    Returns: Entities (name, type, attributes, visual features), Events (label, description, time),
    OCR texts (on-screen text), Key relations (CAUSAL/SPATIAL + key semantic links). When to use:
    Already identified the relevant macro_id, need full details. Always identify the macro_id first
    via search or navigation before calling this.

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
        macro_id: The macro_id to get the subgraph for (e.g. 'macro_0001').
    """
    from qwen_mm_plugins_video_memory.loader import get_toolkit

    args = dict(arguments or {})
    toolkit = get_toolkit(args.pop("video_path", None))
    result = toolkit.get_subgraph(args.get("macro_id", ""))
    return [{"type": "text", "text": result}]
