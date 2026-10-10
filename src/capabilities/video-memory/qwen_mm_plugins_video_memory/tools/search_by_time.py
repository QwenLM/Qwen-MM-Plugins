"""MCP tool: find macro events covering a time range."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from . import VideoPath


class SearchByTimeArgs(BaseModel):
    video_path: VideoPath = None
    start_sec: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    end_sec: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    start_time: Optional[str] = None
    end_time: Optional[str] = None


TOOL = {"name": "search_by_time", "args": SearchByTimeArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Find macro events covering a time range. Returns MacroEvent list with time ranges, parent
    SuperEvent labels, and key entities. When to use: Question references a specific time or time
    range in the video. Use numeric seconds, or start_time/end_time in 'HH:MM:SS' format for a normal
    video and 'DAY{N} HH:MM:SS' for EgoLife. Invalid timestamps or reversed ranges return a tool
    error; they never fall back to a different time window. Note: returns
    macro event summaries, not subgraph details. Always follow up with get_subgraph if you need
    entity/event details.

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
        start_sec: Non-negative start in the memory's seconds timeline. Default: 0.
        end_sec: Non-negative end in the same timeline, at or after the start. Default: 600.
        start_time: Window start as 'HH:MM:SS', or 'DAY{N} HH:MM:SS' for EgoLife.
            A valid string overrides start_sec; an invalid string returns an error.
        end_time: Window end in the same timestamp format. A valid string overrides end_sec;
            an invalid string returns an error.
    """
    from qwen_mm_plugins_video_memory.loader import get_toolkit

    args = dict(arguments or {})
    toolkit = get_toolkit(args.pop("video_path", None))
    result = toolkit.search_by_time(
        start_sec=float(args.get("start_sec", 0)),
        end_sec=float(args.get("end_sec", 600)),
        start_time=args.get("start_time"),
        end_time=args.get("end_time"),
    )
    return [{"type": "text", "text": result}]
