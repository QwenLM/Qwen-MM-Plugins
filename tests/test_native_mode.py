"""Hermetic tests for native MCP images and the text-only caption fallback."""

from __future__ import annotations

import json
import os
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anyio
import pytest
from conftest import mcp_call

import mcp_framework as fw
import shared.api_openai as oa
import shared.env as env
import shared.native_mode as nm


def _response(content):
    message = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _mode(monkeypatch, value: str) -> None:
    monkeypatch.setattr(env, "get_env", lambda name, default=None: value if name == "QWEN_MM_NATIVE_MODE" else default)


def _endpoint(monkeypatch, *, base_url="http://local/v1", api_key="key", model="vl-model") -> None:
    monkeypatch.setattr(oa, "resolve_openai_endpoint", lambda _arguments: (base_url, api_key))
    monkeypatch.setattr(oa, "resolve_vl_model", lambda _model=None: model)


@pytest.fixture
def caption_endpoint():
    """Local OpenAI-compatible endpoint for a network-real, billing-free MCP E2E test."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(json.loads(self.rfile.read(length)))
            payload = json.dumps(
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
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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


def test_native_mode_is_default_zero_work_passthrough(monkeypatch):
    monkeypatch.setattr(env, "get_env", lambda _name, default=None: default)
    blocks = [{"type": "image", "data": "AAAA", "mimeType": "image/png"}]
    monkeypatch.setattr(nm, "_caption_images", lambda _images: pytest.fail("caption API must not run"))

    assert nm.native_mode_enabled() is True
    assert nm.adapt_content_blocks(blocks) is blocks


def test_invalid_mode_fails_safe_to_native(monkeypatch, caplog):
    _mode(monkeypatch, "caption-maybe")
    assert nm.native_mode_enabled() is True
    assert "using default 1" in caplog.text


def test_text_only_mode_without_images_does_not_resolve_endpoint(monkeypatch):
    _mode(monkeypatch, "0")
    monkeypatch.setattr(oa, "resolve_openai_endpoint", lambda _arguments: pytest.fail("no API call expected"))
    blocks = [{"type": "text", "text": "already textual"}]
    assert nm.adapt_content_blocks(blocks) is blocks


@pytest.mark.parametrize(
    "api_key",
    ["", "EMPTY"],
)
def test_missing_api_key_for_custom_endpoint_replaces_images_with_explicit_text(monkeypatch, api_key):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch, base_url="https://custom.example/v1", api_key=api_key)
    monkeypatch.setattr(oa, "call_openai_chat", lambda **_kwargs: pytest.fail("missing key must short-circuit"))
    blocks = [
        {"type": "text", "text": "Image metadata"},
        {"type": "image", "data": "SECRET_BASE64", "mimeType": "image/png"},
    ]

    adapted = nm.adapt_content_blocks(blocks)

    assert adapted[0] == blocks[0]
    assert adapted[1]["type"] == "text"
    assert "requires a non-empty DASHSCOPE_API_KEY" in adapted[1]["text"]
    assert "SECRET_BASE64" not in json.dumps(adapted)


def test_caption_fallback_batches_images_and_preserves_block_order(monkeypatch):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch)
    seen = []

    def fake_call(**kwargs):
        seen.append(kwargs)
        image_parts = [item for item in kwargs["messages"][0]["content"] if item["type"] == "image_url"]
        captions = [f"caption {i + 1}" for i in range(len(image_parts))]
        return _response(json.dumps({"captions": captions}))

    monkeypatch.setattr(oa, "call_openai_chat", fake_call)
    blocks = [{"type": "text", "text": "start"}]
    for i in range(9):
        blocks.extend(
            [
                {"type": "text", "text": f"frame {i}"},
                {"type": "image", "data": f"DATA{i}", "mimeType": "image/png"},
            ]
        )
    blocks.append({"type": "weird", "value": 1})

    adapted = nm.adapt_content_blocks(blocks)

    assert len(seen) == 2  # batch size 8: one request for 8 images, one for the tail
    assert [item["type"] for item in adapted] == [
        "text",
        *[kind for _ in range(9) for kind in ("text", "text")],
        "weird",
    ]
    generated = [item["text"] for item in adapted if item.get("text", "").startswith("[Generated")]
    assert len(generated) == 9
    assert generated[0].endswith("caption 1")
    assert generated[8].endswith("caption 1")  # first (and only) caption in the second batch
    assert seen[0]["model"] == "vl-model"
    assert seen[0]["max_tokens"] == 32 * 1024
    assert seen[1]["max_tokens"] == 32 * 1024
    assert seen[0]["extra_body"] == {"enable_thinking": False}
    assert seen[0]["optional_extra_body"] == {"response_format": {"type": "json_object"}}
    urls = [item["image_url"]["url"] for item in seen[0]["messages"][0]["content"] if item["type"] == "image_url"]
    assert urls[0] == "data:image/png;base64,DATA0"
    assert urls[-1] == "data:image/png;base64,DATA7"


def test_caption_response_accepts_list_content_and_markdown_fence(monkeypatch):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch)
    content = [types.SimpleNamespace(text='```json\n{"captions":["screen text"]}\n```')]
    monkeypatch.setattr(oa, "call_openai_chat", lambda **_kwargs: _response(content))

    adapted = nm.adapt_content_blocks([{"type": "image", "data": "AAAA"}])

    assert adapted == [{"type": "text", "text": "[Generated visual caption]\nscreen text"}]


def test_caption_failure_keeps_existing_text_and_uses_safe_placeholder(monkeypatch, caplog):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch)
    monkeypatch.setattr(oa, "call_openai_chat", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    blocks = [
        {"type": "text", "text": "PDF extracted text"},
        {"type": "image", "data": "AAAA"},
    ]

    adapted = nm.adapt_content_blocks(blocks)

    assert adapted[0] == blocks[0]
    assert "caption generation failed" in adapted[1]["text"]
    assert "AAAA" not in json.dumps(adapted)
    assert "visual caption batch failed" in caplog.text
    assert "boom" not in caplog.text


def test_invalid_caption_response_uses_safe_placeholder(monkeypatch):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch)
    monkeypatch.setattr(oa, "call_openai_chat", lambda **_kwargs: _response('{"captions":[]}'))

    adapted = nm.adapt_content_blocks([{"type": "image", "data": "SECRET_BASE64"}])

    assert "caption generation failed" in adapted[0]["text"]
    assert "SECRET_BASE64" not in json.dumps(adapted)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_permanent_endpoint_error_stops_later_batches(monkeypatch, status_code):
    _mode(monkeypatch, "0")
    _endpoint(monkeypatch)
    calls = 0

    class PermanentEndpointFailure(RuntimeError):
        pass

    error = PermanentEndpointFailure("request body: data:image/png;base64,SECRET_BASE64")
    error.status_code = status_code

    def fail(**_kwargs):
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(oa, "call_openai_chat", fail)
    blocks = [{"type": "image", "data": f"DATA{i}"} for i in range(9)]

    adapted = nm.adapt_content_blocks(blocks)

    assert calls == 1
    assert all(block["text"].startswith("[Visual content unavailable:") for block in adapted)
    assert "SECRET_BASE64" not in json.dumps(adapted)


def test_malformed_image_is_left_for_normal_mcp_validation(monkeypatch):
    _mode(monkeypatch, "0")
    blocks = [{"type": "image", "mimeType": "image/png"}]
    assert nm.adapt_content_blocks(blocks) is blocks


def test_framework_adapts_raw_blocks_before_sdk_conversion(monkeypatch):
    monkeypatch.setattr(
        nm,
        "adapt_content_blocks",
        lambda blocks: [{"type": "text", "text": f"adapted {len(blocks)} block"}],
    )

    async def run():
        return await fw._run_handle(lambda _arguments: [{"type": "image", "data": "AAAA"}], {})

    result = anyio.run(run)

    assert len(result) == 1
    assert result[0].text == "adapted 1 block"


def test_pdf_return_e2e_becomes_caption_text(server_dir, caption_endpoint):
    """PDF renderer → MCP stdio → real HTTP client → caption-only tool result."""
    pytest.importorskip("openai")
    pytest.importorskip("pypdfium2")
    base_url, requests = caption_endpoint
    pdf = Path(__file__).parent / "assets" / "sample.pdf"
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
                "file_path": str(pdf),
                "pages": "1",
                "budget": "small",
                "max_pages": 1,
            },
        ),
        env=env,
    )

    assert not result.isError
    assert result.content
    assert all(block.type == "text" for block in result.content)
    text = "\n".join(block.text for block in result.content)
    assert "[PDF Start]" in text
    assert "[Generated visual caption]\nE2E caption for the rendered PDF page." in text

    assert len(requests) == 1
    request = requests[0]
    image_parts = [part for part in request["messages"][0]["content"] if part["type"] == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/")
    assert request["max_tokens"] == 32 * 1024
    assert request["enable_thinking"] is False
    assert request["response_format"] == {"type": "json_object"}
