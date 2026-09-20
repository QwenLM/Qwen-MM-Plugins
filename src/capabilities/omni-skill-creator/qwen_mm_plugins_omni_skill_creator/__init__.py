"""Omni Skill Creator — 从教学视频提取结构化 Agent Skill 的 MCP 工具集。"""

from mcp_framework import build_registry

__version__ = "1.0.1"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

SYSTEM_DEPS = [
    {
        "label": "ffmpeg (视频/音频片段提取)",
        "extra": None,
        "probe": None,
        "tools": ["ffmpeg", "ffprobe"],
        "hint": "apt install ffmpeg   |   brew install ffmpeg",
        "startup": True,
    },
    {
        "label": "tesseract (OCR 屏幕文字，可选)",
        "extra": None,
        "probe": None,
        "tools": ["tesseract"],
        "hint": "apt install tesseract-ocr   |   brew install tesseract",
        "startup": False,
    },
]
