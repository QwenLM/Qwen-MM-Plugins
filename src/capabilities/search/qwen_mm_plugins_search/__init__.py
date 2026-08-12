"""Qwen-MM-Plugins search: web + reverse-image search for confirming facts.

A pure-tools MCP server. Each module under ``tools/`` exports ``TOOL`` + ``handle`` and is
auto-discovered by the framework. ``web_search`` and ``web_extractor`` support Serper, Exa,
and Tavily through the package-local backend adapters. ``image_search`` uses Serper Lens.
"""

from mcp_framework import build_registry

__version__ = "1.0.2"

# Auto-discover tools from tools/.
SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "Web search + page extraction (Serper / Exa / Tavily) and reverse-image search "
    "(Serper Lens): web_search finds facts, web_extractor reads a page in depth, and "
    "text search auto-selects configured keys in Serper / Tavily / Exa order unless "
    "QWEN_MM_SEARCH_BACKEND pins one provider. "
    "image_search reverse-searches a frame to identify an entity and always uses SERPER_API_KEY, "
    "independently of the text backend. Grab frames with "
    "qwen-mm-plugins-core's save_view first; "
    "cross-check appearance with qwen-mm-plugins-api's vision_chat."
)
