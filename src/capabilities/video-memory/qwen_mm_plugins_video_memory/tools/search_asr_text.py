"""MCP tool: semantic search over ASR (speech transcript) text."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from . import VideoPath


class SearchAsrTextArgs(BaseModel):
    video_path: VideoPath = None
    query: str
    top_k: int = 10


TOOL = {"name": "search_asr_text", "args": SearchAsrTextArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Semantic search over ASR transcript text (speech, dialogue, narration). When to use: Question
    asks about spoken content, dialogue, narration, or any audio/verbal information in the video.
    Returns matching transcript segments with timestamps and macro event context.

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
        query: Search text for spoken content, e.g. 'discussing weather', 'mentions recipe'.
        top_k: Number of results to return (default: 10).
    """
    from qwen_mm_plugins_video_memory.loader import get_toolkit

    args = dict(arguments or {})
    toolkit = get_toolkit(args.pop("video_path", None))
    result = toolkit.search_asr_text(query=args.get("query", ""), top_k=int(args.get("top_k", 10)))
    return [{"type": "text", "text": result}]
