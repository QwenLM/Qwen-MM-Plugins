"""Behavior tests for native MCP images and the text-only caption fallback."""

from __future__ import annotations

import json
import os
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import mcp_call

import shared.api_openai as oa
import shared.env as env
import shared.native_mode as nm


def _mode(monkeypatch, value: str) -> None:
    monkeypatch.setattr(env, "get_env", lambda name, default=None: value if name == "QWEN_MM_NATIVE_MODE" else default)


def _endpoint(monkeypatch, *, api_key="key") -> None:
    monkeypatch.setattr(oa, "resolve_openai_endpoint", lambda _arguments: ("http://local/v1", api_key))
    monkeypatch.setattr(oa, "resolve_vl_model", lambda _model=None: "vl-model")


def _response(captions):
    message = types.SimpleNamespace(content=json.dumps({"captions": captions}))
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


@pytest.fixture
def caption_endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(json.loads(self.rfile.read(length)))
            body = json.dumps(
                {
                    "id": "caption-e2e",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "test-vl",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"captions": ["E2E caption for the rendered PDF page."]}),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_native_mode_passes_images_through_without_resolving_an_endpoint(monkeypatch):
    monkeypatch.setattr(env, "get_env", lambda _name, default=None: default)
    monkeypatch.setattr(oa, "resolve_openai_endpoint", lambda _arguments: pytest.fail("unexpected API setup"))
    blocks = [{"type": "image", "data": "AAAA", "mimeType": "image/png"}]

    assert nm.adapt_content_blocks(blocks) is blocks


def test_text_only_mode_without_images_is_also_a_noop(monkeypatch):
    _mode(monkeypatch, "0")
    monkeypatch.setattr(oa, "resolve_openai_endpoint", lambda _arguments: pytest.fail("unexpected API setup"))
    blocks = [{"type": "text", "text": "already textual"}]

    assert nm.adapt_content_blocks(blocks) is blocks


def test_missing_key_replaces_images_without_exposing_data(monkeypatch):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch, api_key="")
    blocks = [
        {"type": "text", "text": "PDF text"},
        {"type": "image", "data": "SECRET_BASE64"},
    ]

    adapted = nm.adapt_content_blocks(blocks)

    assert adapted[0] == blocks[0]
    assert "requires a non-empty DASHSCOPE_API_KEY" in adapted[1]["text"]
    assert "SECRET_BASE64" not in json.dumps(adapted)


def test_caption_fallback_batches_images_and_preserves_order(monkeypatch):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch)
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        count = sum(part["type"] == "image_url" for part in kwargs["messages"][0]["content"])
        return _response([f"caption {index}" for index in range(count)])

    monkeypatch.setattr(oa, "call_openai_chat", fake_call)
    blocks = [{"type": "text", "text": "start"}] + [
        {"type": "image", "data": f"DATA{index}", "mimeType": "image/png"} for index in range(9)
    ]

    adapted = nm.adapt_content_blocks(blocks)

    assert len(calls) == 2
    assert adapted[0] == blocks[0]
    assert all(block["type"] == "text" for block in adapted)
    assert adapted[1]["text"].endswith("caption 0")
    assert adapted[-1]["text"].endswith("caption 0")
    assert calls[0]["model"] == "vl-model"
    assert calls[0]["max_tokens"] == 32 * 1024
    assert calls[0]["extra_body"] == {"enable_thinking": False}
    assert calls[0]["optional_extra_body"] == {"response_format": {"type": "json_object"}}


def test_caption_failure_uses_safe_placeholders_and_stops(monkeypatch, caplog):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch)
    calls = 0

    def fail(**_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("data:image/png;base64,SECRET_BASE64")

    monkeypatch.setattr(oa, "call_openai_chat", fail)
    adapted = nm.adapt_content_blocks([{"type": "image", "data": f"DATA{index}"} for index in range(9)])

    assert calls == 1
    assert all(block["text"].startswith("[Visual content unavailable:") for block in adapted)
    assert "SECRET_BASE64" not in json.dumps(adapted)
    assert "SECRET_BASE64" not in caplog.text


def test_pdf_return_e2e_becomes_caption_text(server_dir, caption_endpoint):
    pytest.importorskip("openai")
    pytest.importorskip("pypdfium2")
    base_url, requests = caption_endpoint
    env = {
        **os.environ,
        "QWEN_MM_NATIVE_MODE": "0",
        "DASHSCOPE_BASE_URL": base_url,
        "DASHSCOPE_API_KEY": "e2e-placeholder-key",
        "QWEN_MM_API_VL_MODEL": "test-vl",
    }

    result = mcp_call(
        server_dir,
        lambda session: session.call_tool(
            "visualize",
            {
                "file_path": str(Path(__file__).parent / "assets" / "sample.pdf"),
                "pages": "1",
                "budget": "small",
                "max_pages": 1,
            },
        ),
        env=env,
    )

    assert not result.isError
    assert result.content and all(block.type == "text" for block in result.content)
    text = "\n".join(block.text for block in result.content)
    assert "[PDF Start]" in text
    assert "[Generated visual caption]\nE2E caption for the rendered PDF page." in text
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 32 * 1024
    assert requests[0]["enable_thinking"] is False
