"""Configuration failures must reach the MCP caller without preventing discovery."""

import json
import os
from pathlib import Path

import anyio
import pytest
from conftest import CORE_SERVER_DIR, REPO_ROOT, mcp_call

import mcp_framework as fw
from shared import env
from shared.content import text_error


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setenv("QWEN_MM_NATIVE_MODE", "1")


@pytest.mark.parametrize("value", ["90s", "1:30", "-1", "nan", "inf", "1e999"])
def test_cutoff_rejects_invalid_and_nonfinite_values(monkeypatch, value):
    monkeypatch.setenv("CUTOFF_SEC", value)
    with pytest.raises(env.ConfigurationError, match="CUTOFF_SEC"):
        env.get_float_env("CUTOFF_SEC", min_value=0)


@pytest.mark.parametrize("limit,requested,expected", [(1, None, 1), (3, None, 3), (3, 1, 1), (3, 10, 3)])
def test_read_video_honors_runtime_frame_limit(monkeypatch, sample_video, limit, requested, expected):
    from qwen_mm_plugins_core.readers.video import handle

    monkeypatch.setenv("QWEN_MM_MAX_TOTAL_FRAMES", str(limit))
    arguments = {"video_path": sample_video, "fps": 2, "budget": "small"}
    if requested is not None:
        arguments["max_frames"] = requested
    content = handle(arguments)
    assert sum(block["type"] == "image" for block in content) == expected


@pytest.mark.parametrize("value,expected", [("", None), ("0", 0), ("90.5", 90.5)])
def test_cutoff_accepts_blank_zero_and_fractional_seconds(monkeypatch, value, expected):
    monkeypatch.setenv("CUTOFF_SEC", value)
    assert env.get_float_env("CUTOFF_SEC", min_value=0) == expected


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


@pytest.mark.parametrize("value", ["0", "-1", "65536", "1.5", "invalid"])
@pytest.mark.parametrize("capability,name", [("blender", "BLENDER_PORT"), ("freecad", "FREECAD_RPC_PORT")])
def test_invalid_port_fails_before_connecting(monkeypatch, capability, name, value):
    import importlib

    loader = importlib.import_module(f"qwen_mm_plugins_{capability}.loader")
    monkeypatch.setattr(loader, "_connection", None)
    monkeypatch.setenv(name, value)
    with pytest.raises(env.ConfigurationError, match=name):
        loader.get_connection()


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


def test_handler_configuration_error_text_is_preserved(monkeypatch):
    monkeypatch.setenv("CUTOFF_SEC", "90s")

    def catches_error(_arguments):
        try:
            env.get_float_env("CUTOFF_SEC")
        except ValueError as exc:
            return text_error(str(exc))

    result = anyio.run(fw._run_handle, catches_error, {})
    assert result[0].text.startswith("Error:")
    assert "CUTOFF_SEC" in result[0].text
    assert "next call" in result[0].text


@pytest.mark.parametrize(
    "name,tool,argument",
    [
        ("QWEN_MM_FFMPEG_TIMEOUT", "media_info", "path"),
        ("QWEN_MM_MAX_TOTAL_FRAMES", "read_video", "video_path"),
        ("QWEN_MM_NATIVE_MODE", "media_info", "path"),
    ],
)
def test_bad_config_is_visible_over_mcp_without_breaking_discovery(tmp_path, name, tool, argument):
    media = tmp_path / "placeholder.mp4"
    media.write_bytes(b"not probed: configuration must fail first")
    process_env = {**os.environ, name: "invalid-configuration"}

    async def call(session):
        assert tool in {t.name for t in (await session.list_tools()).tools}
        result = await session.call_tool(tool, {argument: str(media)})
        assert (await session.list_tools()).tools
        return result

    result = mcp_call(CORE_SERVER_DIR, call, env=process_env)
    message = "\n".join(block.text for block in result.content if block.type == "text")
    assert name in message
    assert "unset" in message
    assert "invalid-configuration" not in message


@pytest.mark.parametrize("invalid_cutoff", [False, True])
def test_video_memory_returns_actionable_mcp_errors(tmp_path, invalid_cutoff):
    graph = tmp_path / "graph_memory.json"
    graph.write_text(json.dumps({"macro_events": []}), encoding="utf-8")
    process_env = {**os.environ, "GRAPH_MEMORY_PATH": str(graph), "EMBEDDINGS_PATH": "", "CUTOFF_SEC": ""}
    arguments = {"start_time": "bad-timestamp", "end_time": "00:32:00"}
    expected = "start_time"
    if invalid_cutoff:
        process_env["CUTOFF_SEC"] = "90s"
        arguments = {"start_sec": 0, "end_sec": 10}
        expected = "CUTOFF_SEC"
    server = Path(REPO_ROOT) / "src/capabilities/video-memory/qwen_mm_plugins_video_memory"

    async def call(session):
        assert (await session.list_tools()).tools
        failure = await session.call_tool("search_by_time", arguments)
        if not invalid_cutoff:
            retry = await session.call_tool("search_by_time", {"start_time": "00:30:00", "end_time": "00:32:00"})
            assert not retry.isError
            assert json.loads(retry.content[0].text) == []
        return failure

    result = mcp_call(str(server), call, env=process_env)
    assert expected in "\n".join(block.text for block in result.content if block.type == "text")
