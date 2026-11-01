"""Protocol tests for the video-memory MCP server (the second server).

Exercises the SDK bridge end-to-end without a built graph_memory.json: the server
must expose its retrieval tools and fail *gracefully* (an isError result, not a
crash) when no memory is configured. Building/querying real memory is covered
separately (it needs a video + DASHSCOPE_API_KEY).
"""

import os

from conftest import REPO_ROOT, mcp_call

VM_SERVER_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "video-memory", "qwen_mm_plugins_video_memory")

# The retrieval tools the server exposes (auto-discovered from the tools/ subpackage).
VM_TOOLS = {
    "get_macro_events",
    "get_subgraph",
    "search_nodes",
    "search_ocr_text",
    "search_by_time",
    "get_summary",
    "get_super_events",
    "search_asr_text",
    "enumerate_events",
}

# Blank the data-path env vars so the server takes the "no memory configured" path.
_ENV = {**os.environ, "GRAPH_MEMORY_PATH": "", "EMBEDDINGS_PATH": ""}


def test_vm_server_lists_tools():
    result = mcp_call(VM_SERVER_DIR, lambda s: s.list_tools(), env=_ENV)
    assert {t.name for t in result.tools} == VM_TOOLS


def test_vm_server_graceful_without_memory():
    # No video_path + no GRAPH_MEMORY_PATH → the toolkit can't load; the server must
    # surface that as an isError result rather than crashing the process.
    result = mcp_call(VM_SERVER_DIR, lambda s: s.call_tool("search_by_time", {"start_sec": 0, "end_sec": 10}), env=_ENV)
    assert result.isError
    assert any(getattr(b, "type", None) == "text" for b in result.content)
