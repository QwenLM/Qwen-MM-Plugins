"""Cross-plugin behavior: one default policy, visible failures, and recovery without restart."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import anyio
import pytest
from conftest import CORE_SERVER_DIR, REPO_ROOT, mcp_call

import mcp_framework
from qwen_mm_plugins_mhs import registry
from qwen_mm_plugins_mhs.errors import guarded
from qwen_mm_plugins_omni_memory import mem_core, omni_core, service, watch
from qwen_mm_plugins_omni_skill_creator.tools.read_native_av import _default_chat_timeout
from shared import api_omni, api_openai, env


@pytest.fixture(autouse=True)
def config_file(empty_user_config, monkeypatch):
    for key in (
        "QWEN_MM_CHAT_TIMEOUT",
        "QWEN_MM_MHS_CACHE_TTL",
        "MEM_TEMPERATURE",
        "MEM_STREAM_STALL",
        "MEM_WATCH_CALL_TIMEOUT",
        "MEM_BM25_B",
        "MEM_LOCAL_DIR",
        "MEM_ANON_ENTITIES",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "EMBED_API_KEY",
        "EMBED_BASE_URL",
        "EMBED_MODEL_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("QWEN_MM_NATIVE_MODE", "1")
    return empty_user_config


@pytest.mark.parametrize("reader", [api_openai._chat_timeout, api_omni._omni_timeout, _default_chat_timeout])
def test_chat_timeout_has_one_default_and_source_precedence(config_file, monkeypatch, reader):
    assert reader() == 1800
    config_file.write_text("QWEN_MM_CHAT_TIMEOUT=777\n")
    assert reader() == 777
    monkeypatch.setenv("QWEN_MM_CHAT_TIMEOUT", "888")
    assert reader() == 888
    # A blank environment override chooses the default, not the lower-priority file value.
    monkeypatch.setenv("QWEN_MM_CHAT_TIMEOUT", "   ")
    assert reader() == 1800


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "invalid"])
def test_mhs_returns_invalid_ttl_as_actionable_text(config_file, value):
    config_file.write_text(f"QWEN_MM_MHS_CACHE_TTL={value}\n")

    @guarded
    def handle(_):
        registry.cache_ttl()
        return []

    result = anyio.run(mcp_framework._run_handle, handle, {})
    assert result[0].text.startswith("Error:")
    assert "QWEN_MM_MHS_CACHE_TTL" in result[0].text
    assert "next call" in result[0].text
    config_file.write_text("QWEN_MM_MHS_CACHE_TTL=0\n")
    assert anyio.run(mcp_framework._run_handle, handle, {}) == []


@pytest.mark.parametrize(
    "setting,value",
    [
        ("MEM_TEMPERATURE", "invalid"),
        ("MEM_TEMPERATURE", "nan"),
        ("MEM_TEMPERATURE", "-1"),
        ("MEM_TEMPERATURE", "3"),
        ("MEM_STREAM_STALL", "invalid"),
        ("MEM_WATCH_CALL_TIMEOUT", "0"),
    ],
)
def test_omni_config_errors_precede_requests_and_retries(config_file, monkeypatch, setting, value):
    config_file.write_text(f"{setting}={value}\n")
    create = Mock()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    sleep = Mock()
    monkeypatch.setattr(watch, "sleep_note", sleep)
    with pytest.raises(env.ConfigurationError, match=setting):
        watch.watch_answer(client, "data:video/mp4;base64,eA==", "describe")
    create.assert_not_called()
    sleep.assert_not_called()
    config_file.write_text("MEM_TEMPERATURE=0.25\nMEM_ANON_ENTITIES=off\n")
    assert omni_core.temperature_kwargs() == {"temperature": 0.25}
    assert omni_core.anonymous_names() is False


def test_omni_private_config_does_not_block_discovery_and_recovers(config_file, tmp_path):
    video = tmp_path / "sample.mp4"
    memory = Path(str(video) + ".memory")
    memory.mkdir()
    (memory / "store.json").write_text(
        json.dumps(
            {
                "semantic": [{"key": "location", "statement": "robot near table"}],
            }
        )
    )
    config_file.write_text("MEM_BM25_B=invalid\n")
    server = Path(REPO_ROOT) / "src/capabilities/omni-memory/qwen_mm_plugins_omni_memory"

    async def call(session):
        assert "search_memory" in {t.name for t in (await session.list_tools()).tools}
        unrelated = await session.call_tool("get_people", {"video_path": str(video)})
        assert not unrelated.isError
        arguments = {"video_path": str(video), "query": "robot"}
        failed = await session.call_tool("search_memory", arguments)
        assert "MEM_BM25_B" in " ".join(b.text for b in failed.content if b.type == "text")
        config_file.write_text("MEM_BM25_B=0.5\n")
        succeeded = await session.call_tool("search_memory", arguments)
        assert not succeeded.isError
        assert "robot near table" in " ".join(b.text for b in succeeded.content if b.type == "text")

    mcp_call(str(server), call, env=dict(os.environ))


def test_omni_numeric_bounds_apply_at_use(config_file):
    store = mem_core.MemoryStore()
    for value in ("-0.1", "1.1", "nan"):
        config_file.write_text(f"MEM_BM25_B={value}\n")
        with pytest.raises(env.ConfigurationError, match="MEM_BM25_B"):
            store._sparse_rank([{"text": "robot"}], "robot", lambda item: item["text"])
    config_file.write_text("MEM_BM25_B=0\n")
    assert store._sparse_rank([{"text": "robot"}], "robot", lambda item: item["text"]) == [0]


def test_omni_probe_reports_configuration_errors(config_file, monkeypatch):
    from qwen_mm_plugins_omni_memory.tools import watch_and_answer

    monkeypatch.delenv("QWEN_MM_FFMPEG_TIMEOUT", raising=False)
    video = config_file.parent / "video.mp4"
    video.write_bytes(b"configuration must fail before probing")
    config_file.write_text("QWEN_MM_FFMPEG_TIMEOUT=invalid\n")
    with pytest.raises(env.ConfigurationError, match="QWEN_MM_FFMPEG_TIMEOUT"):
        watch_and_answer.handle({"video_path": str(video), "question": "describe"})


def test_omni_clients_follow_file_changes_including_previously_missing_key(config_file, monkeypatch):
    monkeypatch.setattr(service, "_tl", threading.local())
    factory = Mock(side_effect=lambda **_: Mock())
    monkeypatch.setattr(omni_core, "OpenAI", factory)
    assert service.embed_client() is None
    config_file.write_text("DASHSCOPE_API_KEY=first-key\n")
    first = service.embed_client()
    assert service.embed_client() is first
    assert factory.call_count == 1
    config_file.write_text("DASHSCOPE_API_KEY=next-key\nDASHSCOPE_BASE_URL=https://other.example/v1\n")
    assert service.embed_client() is not first
    first.close.assert_called_once()
    assert factory.call_args.kwargs == {"api_key": "next-key", "base_url": "https://other.example/v1"}


def test_invalid_internal_default_is_not_exempt_from_numeric_bounds(config_file):
    with pytest.raises(env.ConfigurationError, match="QMP_PRIVATE_LIMIT"):
        env.get_int_env("QMP_PRIVATE_LIMIT", -1, min_value=0)


@pytest.mark.parametrize("contents", [b"missing-equals\n", b"QWEN_MM_CHAT_TIMEOUT='unfinished\n", b"\xff"])
def test_malformed_file_does_not_hide_tools_and_can_be_repaired(config_file, sample_video, monkeypatch, contents):
    monkeypatch.delenv("QWEN_MM_FFMPEG_TIMEOUT", raising=False)
    config_file.write_bytes(contents)

    async def call(session):
        assert "media_info" in {t.name for t in (await session.list_tools()).tools}
        failed = await session.call_tool("media_info", {"path": sample_video})
        assert "config" in " ".join(block.text for block in failed.content if block.type == "text").lower()
        config_file.write_text("QWEN_MM_FFMPEG_TIMEOUT=120\n")
        assert not (await session.call_tool("media_info", {"path": sample_video})).isError

    mcp_call(CORE_SERVER_DIR, call, env=dict(os.environ))


def test_invalid_search_selection_returns_actionable_text(config_file, monkeypatch):
    monkeypatch.delenv("QWEN_MM_SEARCH_BACKEND", raising=False)
    config_file.write_text("QWEN_MM_SEARCH_BACKEND=unsupported\n")
    server = Path(REPO_ROOT) / "src/capabilities/search/qwen_mm_plugins_search"

    async def call(session):
        result = await session.call_tool("web_search", {"queries": ["offline test"]})
        assert "QWEN_MM_SEARCH_BACKEND" in " ".join(b.text for b in result.content if b.type == "text")

    mcp_call(str(server), call, env=dict(os.environ))


def test_video_memory_imports_with_a_malformed_config_and_recovers(config_file, tmp_path, monkeypatch):
    graph = tmp_path / "graph_memory.json"
    graph.write_text(json.dumps({"macro_events": []}))
    monkeypatch.setenv("GRAPH_MEMORY_PATH", str(graph))
    monkeypatch.delenv("EMBEDDINGS_PATH", raising=False)
    monkeypatch.delenv("CUTOFF_SEC", raising=False)
    config_file.write_text("malformed config line\n")
    server = Path(REPO_ROOT) / "src/capabilities/video-memory/qwen_mm_plugins_video_memory"

    async def call(session):
        assert "search_by_time" in {tool.name for tool in (await session.list_tools()).tools}
        failed = await session.call_tool("search_by_time", {"start_sec": 0, "end_sec": 60})
        assert "line 1" in " ".join(block.text for block in failed.content if block.type == "text")
        config_file.write_text("")
        assert not (await session.call_tool("search_by_time", {"start_sec": 0, "end_sec": 60})).isError

    mcp_call(str(server), call, env=dict(os.environ))
