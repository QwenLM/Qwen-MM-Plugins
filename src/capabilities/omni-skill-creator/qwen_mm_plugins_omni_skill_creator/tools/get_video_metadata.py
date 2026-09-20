"""MCP tool: get video metadata (duration, resolution, codecs, has_audio) via ffprobe."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import MediaOpsError
from ._media_utils import get_video_metadata as _get_metadata


class GetVideoMetadataArgs(BaseModel):
    video_path: str = Field()


TOOL: dict[str, Any] = {"name": "get_video_metadata", "args": GetVideoMetadataArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Get video metadata via ffprobe: duration, resolution, FPS, codecs, has_audio flag, and file size.
    Fast probe — no decoding.

    Args:
        video_path: Path to the source video file.
    """
    raw_path = arguments["video_path"]
    try:
        meta = _get_metadata(raw_path)
    except MediaOpsError as e:
        return [{"type": "text", "text": f"Error: {e}"}]

    return [{"type": "text", "text": json.dumps(meta.__dict__, indent=2)}]
