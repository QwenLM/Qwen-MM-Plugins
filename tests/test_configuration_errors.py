"""Configuration failures must reach the MCP caller without preventing discovery."""

import json
import os
from pathlib import Path

import anyio
import pytest
from conftest import REPO_ROOT, mcp_call

import mcp_framework as fw
from shared import env


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setenv("QWEN_MM_NATIVE_MODE", "1")


def test_boolean_switches_reject_invalid_overrides(monkeypatch):
    from qwen_mm_plugins_freecad.tools import execute_code
    from shared.applaunch import auto_install_enabled, autolaunch_enabled

    for name, reader in [
        ("FREECAD_ONLY_TEXT_FEEDBACK", lambda: execute_code.handle({"code": "pass"})),
        ("QWEN_MM_NO_AUTO_INSTALL", auto_install_enabled),
        ("QWEN_MM_AUTOLAUNCH", autolaunch_enabled),
    ]:
        monkeypatch.setenv(name, "typo")
        with pytest.raises(env.ConfigurationError, match=name):
            reader()


@pytest.mark.parametrize(
    "capability,name,tool",
    [("blender", "BLENDER_PORT", "get_scene_info"), ("freecad", "FREECAD_RPC_PORT", "list_documents")],
)
def test_invalid_startup_port_keeps_mcp_discovery_available(capability, name, tool):
    server = Path(REPO_ROOT) / f"src/capabilities/{capability}/qwen_mm_plugins_{capability}"
    process_env = {**os.environ, name: "65536", "QWEN_MM_AUTOLAUNCH": "0"}

    async def call(session):
        assert tool in {t.name for t in (await session.list_tools()).tools}
        return await session.call_tool(tool, {})

    result = mcp_call(str(server), call, env=process_env)
    assert name in "\n".join(block.text for block in result.content if block.type == "text")


def test_invalid_output_mode_prevents_handler_side_effects(monkeypatch):
    monkeypatch.setenv("QWEN_MM_NATIVE_MODE", "invalid")
    called = []
    with pytest.raises(env.ConfigurationError, match="QWEN_MM_NATIVE_MODE"):
        anyio.run(fw._run_handle, lambda args: called.append(args), {})
    assert called == []


def test_video_memory_returns_actionable_mcp_errors(tmp_path):
    graph = tmp_path / "graph_memory.json"
    graph.write_text(json.dumps({"macro_events": []}), encoding="utf-8")
    process_env = {**os.environ, "GRAPH_MEMORY_PATH": str(graph), "EMBEDDINGS_PATH": "", "CUTOFF_SEC": ""}
    arguments = {"start_time": "bad-timestamp", "end_time": "00:32:00"}
    server = Path(REPO_ROOT) / "src/capabilities/video-memory/qwen_mm_plugins_video_memory"

    async def call(session):
        assert (await session.list_tools()).tools
        failure = await session.call_tool("search_by_time", arguments)
        retry = await session.call_tool("search_by_time", {"start_time": "00:30:00", "end_time": "00:32:00"})
        assert not retry.isError
        assert json.loads(retry.content[0].text) == []
        return failure

    result = mcp_call(str(server), call, env=process_env)
    assert "start_time" in "\n".join(block.text for block in result.content if block.type == "text")
