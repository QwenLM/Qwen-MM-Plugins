"""MCP tool: find macro events covering a time range."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from . import VideoPath


class SearchByTimeArgs(BaseModel):
    video_path: VideoPath = None
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None


TOOL = {"name": "search_by_time", "args": SearchByTimeArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Find macro events covering a time range. Returns MacroEvent list with time ranges, parent
    SuperEvent labels, and key entities. When to use: Question references a specific time or time
    range in the video. Either give seconds (start_sec/end_sec) or the matching timestamp strings
    (start_time/end_time): 'HH:MM:SS' for a normal video, 'DAY{N} HH:MM:SS' for EgoLife (e.g. 'DAY1
    11:09:42', 'DAY3 15:30:00'). Note: returns macro event summaries, not subgraph details. Always
    follow up with get_subgraph if you need entity/event details.

    Args:
        video_path: Path to the video file. Memory auto-loaded from <video_path>.memory/
        start_sec: Start of time window in seconds from video start.
        end_sec: End of time window in seconds from video start.
        start_time: Window start as a timestamp in the memory's own format — 'HH:MM:SS', or 'DAY{N}
            HH:MM:SS' for EgoLife. Overrides start_sec when it parses.
        end_time: Window end in the same format. Overrides end_sec when it parses.
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
