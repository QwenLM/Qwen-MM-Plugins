"""Qwen-MM-Plugins search: web + reverse-image search for confirming facts.

A pure-tools MCP server. Each module under ``tools/`` exports ``TOOL`` + ``handle`` and is
auto-discovered by the framework. All tools call the Serper API (google.serper.dev) via the
package-local ``serper`` client: web_search (text results), web_extractor (page content),
image_search (reverse image / lens). Needs SERPER_API_KEY.
"""

from mcp_framework import build_registry

__version__ = "1.0.1"

# Auto-discover tools from tools/.
SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "Web + reverse-image search (Serper — needs SERPER_API_KEY): web_search finds facts, "
    "web_extractor reads a page in depth, image_search reverse-searches a frame to identify an "
    "entity. Grab frames with qwen-mm-plugins-core's save_view first; cross-check appearance with "
    "qwen-mm-plugins-api's vision_chat."
)
