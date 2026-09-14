"""Exercise creator credentials and timeouts at the HTTP request boundary, offline."""

import json
from types import SimpleNamespace

import httpx
import pytest

from qwen_mm_plugins_omni_skill_creator.tools import read_native_av as av
from shared import env

DASHSCOPE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@pytest.fixture
def connection(monkeypatch):
    for name in (
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_API_KEY",
        "OPENROUTER_API_KEY",
        "ORCAROUTER_API_KEY",
        "QWEN_MM_CHAT_TIMEOUT",
        "QWEN_MM_API_OMNI_MODEL",
        "OMNI_AV_SOURCE_PATH",
        "OMNI_AV_SOURCE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env, "_config_cache", {})
    requests, clients = [], []
    client_type = httpx.Client

    def respond(request):
        requests.append(request)
        event = {"choices": [{"delta": {"content": "complete answer"}, "finish_reason": "stop"}]}
        return httpx.Response(
            200,
            text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    def client(**kwargs):
        clients.append(kwargs)
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(av.httpx, "Client", client)
    return SimpleNamespace(requests=requests, clients=clients)


@pytest.mark.parametrize(
    "base, expected_key",
    [
        (DASHSCOPE, "dash-key"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "dash-key"),
        ("https://openrouter.ai/api/v1", "router-key"),
        ("https://api.orcarouter.ai/v1", "orca-key"),
        ("https://custom.example/v1", "EMPTY"),
        ("https://dashscope.aliyuncs.com.example/v1", "EMPTY"),
    ],
)
def test_handle_sends_only_the_effective_providers_key(connection, monkeypatch, base, expected_key):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", base)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("ORCAROUTER_API_KEY", "orca-key")

    result = av.handle({"video_path": "https://media.example/video.mp4"})

    assert result[0]["text"].startswith("read_native_av |")
    assert str(connection.requests[0].url) == base + "/chat/completions"
    assert connection.requests[0].headers["Authorization"] == f"Bearer {expected_key}"


@pytest.mark.parametrize("inline", [False, True])
def test_private_helper_explicit_connection_overrides_config(connection, monkeypatch, inline):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", DASHSCOPE)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    options = {"base": "https://custom.example/v1", "api_key": "explicit-key"}
    if inline:
        av.perceive_inline("eA==", "video/mp4", "prompt", **options)
    else:
        av.perceive_url("https://media.example/video.mp4", "prompt", **options)
    assert str(connection.requests[0].url) == options["base"] + "/chat/completions"
    assert connection.requests[0].headers["Authorization"] == "Bearer explicit-key"


def test_explicit_base_reselects_provider_key(connection, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", DASHSCOPE)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    av.perceive_url("https://media.example/video.mp4", "prompt", base="https://openrouter.ai/api/v1")
    assert connection.requests[0].headers["Authorization"] == "Bearer router-key"


def test_missing_provider_key_does_not_fall_back_to_dashscope(connection, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    av.handle({"video_path": "https://media.example/video.mp4"})
    assert connection.requests[0].headers["Authorization"] == "Bearer EMPTY"


def test_upload_and_model_use_the_same_connection(connection, monkeypatch, tmp_path):
    path = tmp_path / "large.mp4"
    path.write_bytes(b"placeholder media; uploads are mocked")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", DASHSCOPE)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setattr(av, "_oss_upload_and_sign", lambda path: None)
    monkeypatch.setattr(av, "_fits_inline_budget", lambda path: False)
    uploaded = []

    def upload(path, **kwargs):
        uploaded.append(kwargs)
        return "oss://temporary/video.mp4"

    monkeypatch.setattr(av.dashscope_upload, "upload_temporary_file", upload)
    result = av.handle({"video_path": str(path)})

    assert result[0]["text"].startswith("read_native_av |")
    assert uploaded[0]["base_url"] == DASHSCOPE
    assert uploaded[0]["api_key"] == "dash-key"
    assert connection.requests[0].headers["Authorization"] == "Bearer dash-key"
    assert connection.requests[0].headers["X-DashScope-OssResourceResolve"] == "enable"


@pytest.mark.parametrize("mode", ["remote", "url", "inline", "parts"])
def test_handle_reads_config_file_timeout_on_every_delivery_path(connection, monkeypatch, tmp_path, mode):
    config = tmp_path / "config"
    config.write_text(f"DASHSCOPE_BASE_URL={DASHSCOPE}\nDASHSCOPE_API_KEY=config-key\nQWEN_MM_CHAT_TIMEOUT=17\n")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setattr(env, "_config_cache", None)
    source = "https://media.example/video.mp4"
    if mode != "remote":
        path = tmp_path / "local.mp4"
        path.write_bytes(b"placeholder media; delivery is mocked")
        source = str(path)
        delivered = {
            "url": ("url", "oss://temporary/video.mp4"),
            "inline": ("inline", "eA==", "video/mp4"),
            "parts": ("parts", [{"type": "video_url", "video_url": {"url": "https://media.example/part.mp4"}}]),
        }[mode]
        monkeypatch.setattr(av, "_deliver_local", lambda *a, **kw: delivered)

    result = av.handle({"video_path": source})

    assert result[0]["text"].startswith("read_native_av |")
    assert connection.clients == [{"timeout": 17.0}]
    assert connection.requests[0].headers["Authorization"] == "Bearer config-key"


def test_environment_timeout_overrides_file_at_call_time(connection, monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.write_text("QWEN_MM_CHAT_TIMEOUT=17\n")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setattr(env, "_config_cache", None)
    for value in (23, 29):
        monkeypatch.setenv("QWEN_MM_CHAT_TIMEOUT", str(value))
        av.perceive_url("https://media.example/video.mp4", "prompt")
    assert connection.clients == [{"timeout": 23.0}, {"timeout": 29.0}]


@pytest.mark.parametrize("inline", [False, True])
def test_explicit_timeout_overrides_environment(connection, monkeypatch, inline):
    monkeypatch.setenv("QWEN_MM_CHAT_TIMEOUT", "17")
    if inline:
        av.perceive_inline("eA==", "video/mp4", "prompt", timeout=5.5)
    else:
        av.perceive_url("https://media.example/video.mp4", "prompt", timeout=5.5)
    assert connection.clients == [{"timeout": 5.5}]


@pytest.mark.parametrize("value", [None, "", "invalid", "1.5", "0", "-1"])
def test_unset_or_invalid_timeout_retains_900_seconds(connection, monkeypatch, caplog, value):
    if value is not None:
        monkeypatch.setenv("QWEN_MM_CHAT_TIMEOUT", value)
    av.perceive_url("https://media.example/video.mp4", "prompt")
    assert connection.clients == [{"timeout": 900.0}]
    if value not in (None, ""):
        assert "invalid QWEN_MM_CHAT_TIMEOUT" in caplog.text
