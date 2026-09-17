"""MCP tool: semantic search over Entity and Event nodes (excludes OCR text)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from . import VideoPath


class SearchNodesArgs(BaseModel):
    video_path: VideoPath = None
    query: str
    top_k: int = 10
    node_types: Optional[list[str]] = None


TOOL = {"name": "search_nodes", "args": SearchNodesArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Semantic search over Entity and Event nodes by embedding similarity (excludes OCRText — use
    search_ocr_text for on-screen text). Returns top-k results with relevance scores and ego-graph
    context. When to use: You need to find specific events, actions, or entities but don't know
    which macro to look at. Query Construction Guide: Write a DESCRIPTIVE STATEMENT about the target
    event, NOT a question — the embedding matches event descriptions, not questions. Extract key
    entities and actions from the question (person names, action types, objects). Extract SHARED
    information from all options — keywords/phrases that appear across multiple options are the most
    reliable retrieval signal. EXCLUDE speculative differing details from options — if options
    disagree on a specific detail (e.g., different numbers, different names), do NOT pick one;
    instead describe the event type neutrally. Good: 'A player scores with an alley-oop dunk' —
    descriptive, captures the shared event type. Bad: 'Which player scored the alley-oop?' —
    question format, poor match with event descriptions. Good: 'A foul is committed on a specific
    player by an opposing player, leading to free throws' — captures shared info from options (foul
    + free throws) while staying neutral on the differing detail. Bad: 'Australia No. 7 fouled' —
    picks one specific option's detail, may mislead if wrong.

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
        query: Descriptive statement for semantic search (NOT a question). Combine key entities,
            actions, and shared option info into a complete sentence.
        top_k: Number of results to return (default: 10).
        node_types: Filter by node type (lowercase). Valid values: 'event', 'entity',
            'on_screen_text'. e.g. ['event'], ['entity'].
    """
    from qwen_mm_plugins_video_memory.loader import get_toolkit

    args = dict(arguments or {})
    toolkit = get_toolkit(args.pop("video_path", None))
    result = toolkit.search_nodes(
        query=args.get("query", ""),
        top_k=int(args.get("top_k", 10)),
        node_types=args.get("node_types"),
    )
    return [{"type": "text", "text": result}]
