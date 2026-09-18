"""Offline regressions for Omni request retries, cancellation, and config refresh."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import openai
import pytest

transport = importlib.import_module("qwen_mm_plugins_omni_video2note.pipeline.omni_client")
config_module = importlib.import_module("qwen_mm_plugins_omni_video2note.pipeline.config")


def make_config(*, budget=5.0, base_url="https://example.invalid/v1"):
    return SimpleNamespace(
        deadline=None,
        time_budget_seconds=budget,
        api_calls=[],
        warnings=[],
        _base_url=base_url,
        _api_key="test-secret-do-not-report",
        omni_model="test-omni",
    )


class FakeStream:
    def __init__(self, actions):
        self.actions = actions
        self.closed = False

    async def __aiter__(self):
        for value in self.actions:
            if isinstance(value, Exception):
                raise value
            yield SimpleNamespace(
                usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6, "secret": "hidden"},
                choices=[SimpleNamespace(delta=SimpleNamespace(content=value))],
            )

    async def close(self):
        self.closed = True


def install_client(monkeypatch, actions):
    clients, streams, calls = [], [], []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.closed = False
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            clients.append(self)

        async def create(self, **kwargs):
            calls.append(kwargs)
            action = actions[len(calls) - 1]
            if isinstance(action, Exception):
                raise action
            stream = FakeStream(action)
            streams.append(stream)
            return stream

        async def close(self):
            self.closed = True

    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    return clients, streams, calls


def invoke(config):
    return transport.call_omni_text(config, messages=[{"role": "user", "content": "hello"}], stage="test")


def request_error(status):
    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(status, request=request)
    return openai.APIStatusError("test-secret-do-not-report", response=response, body={"secret": "hidden"})


def test_omni_protocol_and_safe_metrics(monkeypatch):
    clients, streams, calls = install_client(monkeypatch, [["ok"]])
    config = make_config()
    assert invoke(config) == "ok"
    assert len(calls) == 1
    assert calls[0]["model"] == config.omni_model
    assert calls[0]["stream"] is True
    assert calls[0]["extra_body"] == {"modalities": ["text"]}
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert "response_format" not in calls[0]
    assert all(client.closed for client in clients)
    assert all(stream.closed for stream in streams)
    assert config.api_calls[0]["status"] == "success"
    assert config.api_calls[0]["usage"] == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
    assert "secret" not in json.dumps(config.api_calls)


@pytest.mark.parametrize("failure", ["timeout", "connection", "empty", 429, 500, 502, 503, 504])
def test_transient_errors_retry_once_and_close_clients(monkeypatch, failure):
    request = httpx.Request("POST", "https://example.invalid")
    error = {
        "timeout": openai.APITimeoutError(request=request),
        "connection": openai.APIConnectionError(request=request),
        "empty": [],
    }.get(failure)
    if isinstance(failure, int):
        error = request_error(failure)
    clients, streams, calls = install_client(monkeypatch, [error, ["ok"]])
    config = make_config()
    assert invoke(config) == "ok"
    assert len(calls) == 2
    assert len(config.api_calls) == 2
    assert config.warnings
    assert all(client.closed for client in clients)
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_http_errors_do_not_retry_or_expose_body(monkeypatch, status):
    clients, _, calls = install_client(monkeypatch, [request_error(status)])
    with pytest.raises(transport.OmniCallError) as raised:
        invoke(make_config())
    assert raised.value.status_code == status
    assert "test-secret" not in str(raised.value)
    assert "hidden" not in str(raised.value)
    assert len(calls) == 1
    assert all(client.closed for client in clients)


def test_retry_limit_is_two_total_attempts(monkeypatch):
    clients, _, calls = install_client(monkeypatch, [request_error(503), request_error(503)])
    with pytest.raises(transport.OmniCallError, match="HTTP 503"):
        invoke(make_config())
    assert len(calls) == 2
    assert all(client.closed for client in clients)


def test_interrupted_response_preserves_partial_without_new_request(monkeypatch):
    request = httpx.Request("POST", "https://example.invalid")
    clients, streams, calls = install_client(
        monkeypatch,
        [["useful partial note", openai.APIConnectionError(message="test-secret-do-not-report", request=request)]],
    )
    config = make_config()
    with pytest.raises(transport.OmniCallError) as raised:
        invoke(config)
    assert raised.value.partial_text == "useful partial note"
    assert raised.value.reason == "connection_error"
    assert len(calls) == 1
    assert config.api_calls[0]["partial"] is True
    assert all(client.closed for client in clients)
    assert all(stream.closed for stream in streams)


def test_exhausted_budget_does_not_open_client(monkeypatch):
    _, _, calls = install_client(monkeypatch, [])
    config = make_config()
    config.deadline = time.monotonic() - 1
    with pytest.raises(transport.OmniCallError, match="budget exhausted"):
        invoke(config)
    assert calls == []


@pytest.mark.parametrize("inside_event_loop", [False, True])
def test_incomplete_sse_bytes_cannot_extend_absolute_deadline(monkeypatch, inside_event_loop):
    """A per-read timeout resets on bytes; cancellation must also bound unfinished SSE events."""
    requests = []

    class SlowSSE(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(b'data: {"choices":')
                self.wfile.flush()
                for _ in range(26):
                    time.sleep(0.05)
                    self.wfile.write(b" ")
                    self.wfile.flush()
                time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowSSE)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(transport, "_REQUEST_TIMEOUT_SECONDS", 0.65)
        config = make_config(budget=0.65, base_url=f"http://127.0.0.1:{server.server_port}/v1")

        async def nested():
            return invoke(config)

        started = time.monotonic()
        with pytest.raises(transport.OmniCallError, match="budget exhausted"):
            asyncio.run(nested()) if inside_event_loop else invoke(config)
        elapsed = time.monotonic() - started
        assert elapsed < 1.2, f"incomplete SSE escaped deadline: {elapsed:.3f}s"
        assert len(requests) == 1
        assert config.api_calls[0]["status"] == "timeout"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=0.2)


def test_config_refreshes_file_model_and_endpoint_when_key_is_in_environment(monkeypatch, tmp_path):
    from shared import env

    key_name = next(name for name, *_ in env.CONFIG_FIELDS if name.endswith("_API_KEY"))
    model_name = next(name for name, *_ in env.CONFIG_FIELDS if name.endswith("_API_OMNI_MODEL"))
    base_name = next(name for name, *_ in env.CONFIG_FIELDS if name.endswith("_BASE_URL"))
    monkeypatch.setenv(key_name, "test-key-from-environment")
    monkeypatch.delenv(model_name, raising=False)
    monkeypatch.delenv(base_name, raising=False)
    config_file = tmp_path / "config"
    monkeypatch.setattr(env, "config_file", lambda: str(config_file))
    monkeypatch.setattr(env, "_config_cache", {model_name: "stale-model", base_name: "https://stale.invalid"})
    config_file.write_text(f'{model_name}="fresh-omni"\n{base_name}="https://fresh.invalid/v1"\n')
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    config = config_module.PipelineConfig(video, tmp_path / "note.pdf", vl_model="ignored-vl")
    assert config.omni_model == config.vl_model == config.review_model == "fresh-omni"
    assert config._base_url == "https://fresh.invalid/v1"
    assert config._api_key == "test-key-from-environment"
    assert config.quality_profile == "fast"
    assert config.time_budget_seconds == 150
    assert config.warnings
    assert config._api_key not in json.dumps(config.to_dict())
