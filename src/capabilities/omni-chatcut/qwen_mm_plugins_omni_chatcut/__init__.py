"""Omni ChatCut: atomic tools and runtime for project-backed video creation."""

__version__ = "1.0.2"

from mcp_framework import build_registry

SPECS, get_handler, list_tools = build_registry(
    __name__,
    ["music_to_mv.tools", "movie_commentary.tools", "video_translation.tools"],
)

SYSTEM_DEPS = [
    {
        "label": "audio slicing, media probing, dubbing, and video assembly",
        "tools": ["ffmpeg", "ffprobe"],
        "hint": "apt install ffmpeg   |   brew install ffmpeg",
    }
]

USAGE_NOTE = (
    "Use the independently discoverable Omni ChatCut Skills for Music-to-MV, movie commentary, "
    "or video translation; these MCP tools provide their atomic media operations."
)
