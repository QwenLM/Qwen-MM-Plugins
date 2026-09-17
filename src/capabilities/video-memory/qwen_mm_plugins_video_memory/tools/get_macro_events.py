"""MCP tool: list macro events (detailed events within a super event)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from . import VideoPath


class GetMacroEventsArgs(BaseModel):
    video_path: VideoPath = None
    super_id: Optional[str] = None
    macro_ids: Optional[list[str]] = None


TOOL = {"name": "get_macro_events", "args": GetMacroEventsArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get Macro Event list (detailed events within a Super Event). Each macro event has macro_id,
    time_range, label, key_entities, summary. When to use: You know which SuperEvent is relevant but
    need to find the right MacroEvent within it. If super_id is omitted, lists ALL macro events.

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
        super_id: Filter by super event ID, e.g. 'super_01'. Optional — omit to list all.
        macro_ids: List of specific macro IDs to retrieve, e.g. ['macro_0001', 'macro_0002'].
    """
    from qwen_mm_plugins_video_memory.loader import get_toolkit

    args = dict(arguments or {})
    toolkit = get_toolkit(args.pop("video_path", None))
    result = toolkit.get_macro_events(super_id=args.get("super_id", ""), macro_ids=args.get("macro_ids"))
    return [{"type": "text", "text": result}]
