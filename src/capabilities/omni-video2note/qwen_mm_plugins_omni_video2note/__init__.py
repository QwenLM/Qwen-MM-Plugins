"""Omni Video2Note MCP server: turn local tutorial videos into audited illustrated PDFs."""

__version__ = "1.0.2"

from mcp_framework import build_registry

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

SYSTEM_DEPS = [
    {
        "label": "video probing and frame/audio extraction (ffmpeg/ffprobe)",
        "tools": ["ffmpeg", "ffprobe"],
        "hint": "apt install ffmpeg   |   brew install ffmpeg",
        "startup": True,
    },
    {
        "label": "optional PDF preview rendering (pdftoppm)",
        "tools": ["pdftoppm"],
        "hint": "apt install poppler-utils   |   brew install poppler",
        "startup": False,
    },
]
