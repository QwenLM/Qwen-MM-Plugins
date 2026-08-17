"""End-to-end: proxy -> mock upstream, all three inbound protocols."""

from __future__ import annotations

import json
import threading

import httpx
import pytest
from conftest import RecordingUpstream
from qwen_mm_plugins_proxy.cache import DescriptionCache
from qwen_mm_plugins_proxy.config import ProxyConfig, RelayConfig, VLMConfig
from qwen_mm_plugins_proxy.pipeline import Pipeline
from qwen_mm_plugins_proxy.server import run_server


class FakeVLM:
    """Stub VLM: returns a deterministic description string."""

    def describe(self, image, question=None, tier=1):
        return "integration description"


@pytest.fixture()
def stack():
    up = RecordingUpstream().start()
    base = f"http://127.0.0.1:{up.port}/v1"
    # Three relays so _select_relay can match every inbound protocol;
    # all point at the same mock upstream.
    cfg = ProxyConfig(
        bind_port=0,  # ephemeral: avoid colliding with the default 8787 (or another test run)
        relays=[
            RelayConfig(name="anth", protocol="anthropic", base_url=base),
            RelayConfig(name="resp", protocol="responses", base_url=base),
            RelayConfig(name="chat", protocol="chat", base_url=base),
        ],
        vlm=VLMConfig(model="qwen-vl-max"),
    )
    srv = run_server(cfg)
    # Replace the real VLM pipeline with one backed by FakeVLM
    srv.pipeline = Pipeline(FakeVLM(), DescriptionCache())
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, up
    srv.shutdown()
    up.stop()


def _post(port, path, body):
    # 绕过宿主系统代理：localhost 测试服务器不能被透明代理劫持成 502
    return httpx.Client(trust_env=False).post(f"http://127.0.0.1:{port}{path}", json=body, timeout=10)


def test_anthropic_inbound_image_replaced(stack):
    port, up = stack
    resp = _post(
        port,
        "/v1/messages",
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
                    ],
                }
            ],
        },
    )
    assert resp.status_code == 200
    raw = json.dumps(up.received[-1])
    assert "integration description" in raw
    assert "QUJD" not in raw.replace("integration description", "")


def test_responses_inbound_function_output_data_url_stripped(stack):
    port, up = stack
    resp = _post(
        port,
        "/v1/responses",
        {
            "model": "deepseek-v4-pro",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "check"}]},
                {"type": "function_call_output", "call_id": "c1", "output": "data:image/png;base64,QUJD"},
            ],
        },
    )
    assert resp.status_code == 200
    raw = json.dumps(up.received[-1])
    assert "integration description" in raw
    assert "base64,QUJD" not in raw


def test_chat_inbound_image_url_replaced(stack):
    port, up = stack
    resp = _post(
        port,
        "/v1/chat/completions",
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                    ],
                }
            ],
        },
    )
    assert resp.status_code == 200
    raw = json.dumps(up.received[-1])
    assert "integration description" in raw
    assert "base64,QUJD" not in raw
