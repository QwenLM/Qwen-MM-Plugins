from __future__ import annotations

from typing import Any

from pydantic import Field

from shared.content import json_text

from ..service import load_store, memory_label, moment_brief
from . import MemoryRef


class GetTimelineArgs(MemoryRef):
    start_sec: float = Field(default=0, ge=0)
    end_sec: float = Field(default=0, ge=0)


TOOL = {"name": "get_timeline", "args": GetTimelineArgs}


def get_timeline(
    video_path: str | None = None, namespace: str | None = None, start_sec: float = 0, end_sec: float = 0
) -> dict[str, Any]:
    store = load_store(video_path, namespace)
    # end_sec=0 (the default) means "to the end" rather than an empty window.
    end = float(end_sec) or max((c.get("win_end", 0) for c in (store.clips or [])), default=0.0)
    eps = store.episodic_in_time(float(start_sec), end) or []
    return {
        "label": memory_label(video_path, namespace),
        "range_sec": [float(start_sec), end],
        "count": len(eps),
        "moments": [moment_brief(store, o) for o in eps],
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Moments within a time window, in order — one brief per 30s clip (caption, who is present, how
    many lines were spoken). Use it for 'what happens around 12:30' or to walk a stretch of the
    video; then call get_moment on the interesting idxs.

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
        start_sec: Window start in video seconds.
        end_sec: Window end in video seconds. 0 means through the end of the video.
    """
    return [json_text(get_timeline(**arguments))]
