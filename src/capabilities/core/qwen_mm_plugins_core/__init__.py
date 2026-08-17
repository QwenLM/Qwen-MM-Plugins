"""Qwen-MM-Plugins: unified MCP server for vision-language models.

Server config: the auto-discovered tool registry (SPECS), the streaming transport hook, the
system-tool table (SYSTEM_DEPS), and the --help caption (USAGE_NOTE) — the last two read by run_main.
"""

from mcp_framework import build_registry

from .stdio_streaming import streaming_stdio_server

__version__ = "1.0.3"

# Auto-discover tools from these subpackages. Cloud API tools live in the qwen-mm-plugins-api
# (vision_chat/ocr/grounding/segmentation/transcribe_audio) and qwen-mm-plugins-search
# (web_search/web_extractor/image_search) capabilities — core is local file I/O only.
SPECS, get_handler, list_tools = build_registry(__name__, ["readers", "visualizers", "producers"])

# Streaming stdio transport — keeps peak memory near one frame for large read_video results.
transport = streaming_stdio_server

# System tools pip/uv cannot install; the framework renders --check-system + startup warnings from
# this table. Required per entry: label, tools, hint. Optional: extra (pip group for its Python
# side), probe (import name gating it), startup (False = report-only). See the SYSTEM_DEPS engine in
# mcp_framework for the full field contract.
SYSTEM_DEPS = [
    {
        "label": "read_video / media_info (video & audio)",
        "tools": ["ffmpeg", "ffprobe"],
        "hint": "apt install ffmpeg   |   brew install ffmpeg",
    },
    {
        "label": "visualize: 3D best-quality render (Blender; else falls back to matplotlib)",
        "extra": "viz",
        "probe": "trimesh",
        "tools": ["blender"],
        "hint": "apt install blender   |   brew install --cask blender",
    },
    {
        "label": "visualize: Office / DrawIO (LibreOffice)",
        "extra": "viz",
        "probe": "pypdfium2",
        "tools": ["libreoffice", "soffice"],
        "hint": "apt install libreoffice   |   brew install --cask libreoffice",
    },
    {
        "label": "visualize: LaTeX (.tex)",
        "extra": "system",  # no pip side — pdflatex is system-only (texlive)
        "tools": ["pdflatex"],
        "hint": "apt install texlive-latex-base texlive-latex-extra",
        "startup": False,  # system-only — report-only, don't nag every startup
    },
    {
        "label": "visualize: HTML screenshot (Playwright browser)",
        "extra": "viz",
        "probe": "playwright",
        "tools": ["__playwright_chromium__"],  # not a PATH binary; see hint
        "hint": "playwright install chromium",
        "startup": False,  # can't reliably detect browser — report-only
    },
]

# Server-specific tail for --help / no-tty usage.
USAGE_NOTE = (
    "Install via your harness's plugin marketplace (qwen-mm-plugins-core@qwen-mm-plugins),\n"
    "or see the repo README for the manual skill-link + mcp-add steps per harness."
)
