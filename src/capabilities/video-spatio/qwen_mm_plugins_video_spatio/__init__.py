"""Qwen-MM-Plugins video-spatio: model-free 3D spatial reasoning — a skill plus stateless
geometry/VLM MCP tools (build_scene, triangulate, visualize_bev, camera_motion, orient_facing, ...)
driven by the host model. No inner agent and no perception (GPU) server.
"""

from mcp_framework import build_registry

__version__ = "1.0.0"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

SYSTEM_DEPS = []
SYSTEM_DEPS_NOTE = "  Model-free / prompt-only: no external perception (GPU) server required."

USAGE_NOTE = (
    "Flattened: skill + stateless tools driven by the OUTER model (which does perception itself). "
    "Geometry tools (build_scene/triangulate/visualize_bev/...) need no API key. The few single-shot "
    "VLM tools (orient_facing/verify_grounding/...) reuse the MAIN model endpoint by default "
    "(MODEL_BASE_URL/MODEL_API_KEY/MODEL) — Anthropic-native (routify /protocol/anthropic) auto-detected, "
    "OpenAI-compatible otherwise. Override with VIDEO_SPATIO_VLM_BASE_URL/_API_KEY/_MODEL."
)
