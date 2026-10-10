"""Real config-file edits affect subsequent calls in the same process and MCP session."""

import json
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from conftest import CORE_SERVER_DIR, REPO_ROOT, mcp_call

import mcp_framework as fw
import qwen_mm_plugins_core as core
from qwen_mm_plugins_core.readers import video as reader
from shared import env, video


@pytest.fixture
def config_file(empty_user_config, monkeypatch):
    for name in ("QWEN_MM_NATIVE_MODE", "QWEN_MM_FFMPEG_TIMEOUT", "QWEN_MM_MAX_TOTAL_FRAMES", "CUTOFF_SEC"):
        monkeypatch.delenv(name, raising=False)
    return empty_user_config


def replace_config(path, **values):
    staging = path.with_suffix(".new")
    staging.write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")
    staging.replace(path)


@pytest.mark.parametrize(
    "setting,tool,argument,value",
    [
        ("QWEN_MM_FFMPEG_TIMEOUT", "media_info", "path", "42"),
        ("QWEN_MM_MAX_TOTAL_FRAMES", "read_video", "video_path", "3"),
        ("QWEN_MM_NATIVE_MODE", "media_info", "path", "1"),
    ],
)
def test_config_file_repair_recovers_in_same_mcp_session(config_file, sample_video, setting, tool, argument, value):
    replace_config(config_file, **{setting: "invalid"})

    async def call(session):
        assert tool in {tool.name for tool in (await session.list_tools()).tools}
        failed = await session.call_tool(tool, {argument: sample_video})
        message = "\n".join(block.text for block in failed.content if block.type == "text")
        assert setting in message and "next call" in message
        replace_config(config_file, **{setting: value})
        succeeded = await session.call_tool(tool, {argument: sample_video})
        assert not succeeded.isError
        if tool == "read_video":
            assert sum(block.type == "image" for block in succeeded.content) == 3

    mcp_call(CORE_SERVER_DIR, call, env=dict(os.environ))


def test_cutoff_file_edits_update_cached_toolkit_without_reconnect(config_file, tmp_path):
    graph = tmp_path / "graph_memory.json"
    graph.write_text(
        json.dumps({"macro_events": [{"macro_id": "one", "label": "Opening", "time_range": [0, 30]}]}),
        encoding="utf-8",
    )
    replace_config(config_file, CUTOFF_SEC="bad")
    server = Path(REPO_ROOT) / "src/capabilities/video-memory/qwen_mm_plugins_video_memory"
    process_env = {**os.environ, "GRAPH_MEMORY_PATH": str(graph), "EMBEDDINGS_PATH": ""}

    async def call(session):
        failed = await session.call_tool("search_by_time", {"start_sec": 0, "end_sec": 40})
        assert "CUTOFF_SEC" in " ".join(block.text for block in failed.content if block.type == "text")
        for cutoff, expected in [("60", ["one"]), ("0", []), ("", ["one"])]:
            replace_config(config_file, CUTOFF_SEC=cutoff)
            result = await session.call_tool("search_by_time", {"start_sec": 0, "end_sec": 40})
            assert not result.isError
            assert [event["macro_id"] for event in json.loads(result.content[0].text)] == expected

    mcp_call(str(server), call, env=process_env)


def test_frame_cap_increases_decreases_and_stays_fixed_within_a_call(config_file, tmp_path, monkeypatch):
    media = tmp_path / "placeholder.mp4"
    media.write_bytes(b"probed and decoded by offline stand-ins")
    change_during_probe = []

    def probe(_):
        if change_during_probe:
            replace_config(config_file, QWEN_MM_MAX_TOTAL_FRAMES=change_during_probe.pop())
        return {"duration": 1000, "native_fps": 30, "height": 180, "width": 320}

    monkeypatch.setattr(reader, "get_video_info", probe)
    monkeypatch.setattr(reader, "extract_frames_by_seeking", lambda _path, times, *_: [(t, "eA==") for t in times])
    # Construct the MCP wrapper once, before all edits: a frozen Pydantic default of 600 would
    # prevent a later increase from taking effect when max_frames is omitted.
    wrapper = fw._make_wrapper(next(spec for spec in core.SPECS if spec.name == "read_video"))

    def read(requested=None):
        arguments = {"video_path": str(media), "fps": 2}
        if requested is not None:
            arguments["max_frames"] = requested
        result = anyio.run(partial(wrapper, **arguments))
        return sum(block.type == "image" for block in result)

    for limit, requested, expected in [
        (600, None, 600),
        (1000, None, 1000),
        (1000, 700, 700),
        (3, None, 3),
        (3, 700, 3),
    ]:
        replace_config(config_file, QWEN_MM_MAX_TOTAL_FRAMES=limit)
        assert read(requested) == expected

    replace_config(config_file, QWEN_MM_MAX_TOTAL_FRAMES=5)
    change_during_probe.append(2)
    assert read() == 5
    assert read() == 2


def test_ffprobe_timeout_changes_for_the_next_subprocess(config_file, monkeypatch):
    timeouts = []

    def run(_command, **kwargs):
        timeouts.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout='{"streams": [], "format": {}}')

    monkeypatch.setattr(video, "find_tool", lambda name: name)
    monkeypatch.setattr(video.subprocess, "run", run)
    for timeout in (17, 42):
        replace_config(config_file, QWEN_MM_FFMPEG_TIMEOUT=timeout)
        video.probe_media("placeholder.mp4")
    assert timeouts == [17, 42]


def test_queued_frame_workers_read_the_current_timeout(config_file, monkeypatch):
    timeouts = []

    def run(_command, **kwargs):
        timeouts.append(kwargs["timeout"])
        if len(timeouts) == 1:
            replace_config(config_file, QWEN_MM_FFMPEG_TIMEOUT=42)
        return SimpleNamespace(returncode=0, stdout=b"image")

    monkeypatch.setattr(video, "find_tool", lambda name: name)
    monkeypatch.setattr(video.subprocess, "run", run)
    replace_config(config_file, QWEN_MM_FFMPEG_TIMEOUT=17)
    video.extract_frames_by_seeking("placeholder.mp4", [0, 1, 2], 0, 0, max_workers=1)
    assert timeouts == [17, 42, 42]


def test_hot_invalid_timeout_in_worker_reaches_caller(config_file, monkeypatch):
    def run(_command, **_kwargs):
        replace_config(config_file, QWEN_MM_FFMPEG_TIMEOUT="bad")
        return SimpleNamespace(returncode=0, stdout=b"image")

    monkeypatch.setattr(video, "find_tool", lambda name: name)
    monkeypatch.setattr(video.subprocess, "run", run)
    replace_config(config_file, QWEN_MM_FFMPEG_TIMEOUT=17)
    with pytest.raises(env.ConfigurationError, match="QWEN_MM_FFMPEG_TIMEOUT"):
        video.extract_frames_by_seeking("placeholder.mp4", [0, 1], 0, 0, max_workers=1)
