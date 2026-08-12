"""Qwen-MM-Plugins video-edit: image/video/audio generation tools (DashScope), paired with the
ffmpeg-based video-editing skill.
"""

from mcp_framework import build_registry

__version__ = "1.0.1"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

# ffmpeg is for the video-edit *skill*, not the MCP tools — report at install time only (startup:False).
SYSTEM_DEPS = [
    {
        "label": "video-edit skill: local editing / frame ops (ffmpeg)",
        "extra": "video-edit",
        "tools": ["ffmpeg", "ffprobe"],
        "hint": "apt install ffmpeg   |   brew install ffmpeg",
        "startup": False,
    },
]
SYSTEM_DEPS_NOTE = "  The generation MCP tools (image/tts/video) call remote APIs — no system tools needed."

USAGE_NOTE = (
    "Generation tools (qwen_image / qwen_tts / wan_s2v / wan_t2v / happyhorse) via DashScope.\n"
    "Needs DASHSCOPE_API_KEY (inherited from the shell)."
)
