"""MCP tool: semantic search over OCR (on-screen text) nodes only."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from . import VideoPath


class SearchOcrTextArgs(BaseModel):
    video_path: VideoPath = None
    query: str
    top_k: int = 10


TOOL = {"name": "search_ocr_text", "args": SearchOcrTextArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Semantic search over OCR text nodes only (on-screen text: scores, names, graphics, jersey
    numbers, broadcast overlays). When to use: Question asks about on-screen information (scores,
    player stats, quarter info, jersey names/numbers, broadcast graphics). Do NOT use search_nodes
    for these — OCR text is excluded from search_nodes.

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
        query: Search text, e.g. 'scoreboard', 'third quarter score', 'jersey number'.
        top_k: Number of results to return (default: 10).
    """
    from qwen_mm_plugins_video_memory.loader import get_toolkit

    args = dict(arguments or {})
    toolkit = get_toolkit(args.pop("video_path", None))
    result = toolkit.search_ocr_text(query=args.get("query", ""), top_k=int(args.get("top_k", 10)))
    return [{"type": "text", "text": result}]
