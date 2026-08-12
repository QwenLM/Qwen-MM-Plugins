"""Qwen-MM-Plugins example: a minimal reference MCP server — the "how to add a capability" template."""

__version__ = "1.0.0"
from mcp_framework import build_registry

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

# System tools pip/uv cannot install — the framework renders --check-system + startup warnings from
# this table. A minimal entry is just label + tools + hint (a system binary with no Python gating);
# optional fields (extra / probe / startup) are documented in the SYSTEM_DEPS engine in mcp_framework.
# This template's demo tools are pure-Python (PIL + OpenAI client), so the table is empty — with no
# entries, --check-system prints "No system tools required." If your capability shells out to a
# system binary, add an entry here, e.g.:
#   {"label": "video frame extraction", "tools": ["ffmpeg"], "hint": "apt install ffmpeg   |   brew install ffmpeg"}
SYSTEM_DEPS: list[dict] = []

USAGE_NOTE = "A template capability — see docs/en/how_to_add_new_capability.md for how to add your own."
