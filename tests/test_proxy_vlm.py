from __future__ import annotations

import json

import httpx
import pytest
from qwen_mm_plugins_proxy.config import VLMConfig
from qwen_mm_plugins_proxy.ir import ImageBlock
from qwen_mm_plugins_proxy.vlm import VLMClient, VLMError, probe_ollama


def _client_with(transport) -> VLMClient:
    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1", api_key="k")
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=transport)
    return client


def test_describe_chat_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "qwen-vl-max"
        assert body["messages"][0]["content"][1]["type"] == "image_url"
        return httpx.Response(200, json={"choices": [{"message": {"content": "一只橘猫"}}]})

    client = _client_with(httpx.MockTransport(handler))
    assert client.describe(ImageBlock(url="data:image/png;base64,QUJD")) == "一只橘猫"


def test_describe_http_error_raises_vlm_error():
    client = _client_with(httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad key"})))
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="data:image/png;base64,QUJD"))
    assert exc.value.reason == "AUTH"


def test_probe_ollama_finds_vision_model(monkeypatch):
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3-vl:4b"}, {"name": "llama3"}]})

    monkeypatch.setattr(
        "qwen_mm_plugins_proxy.vlm.httpx.Client",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler)),
    )
    assert probe_ollama() == "qwen3-vl:4b"


def _anthropic_client_with(transport) -> VLMClient:
    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1", api_key="k", format="anthropic")
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=transport)
    return client


def test_describe_chat_missing_choices_raises_parse():
    client = _client_with(httpx.MockTransport(lambda r: httpx.Response(200, json={"foo": "bar"})))
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="[图片描述失败，视觉模型调用失败]"))
    assert exc.value.reason == "PARSE"


def test_describe_chat_top_level_list_raises_parse():
    client = _client_with(httpx.MockTransport(lambda r: httpx.Response(200, json=[{"choices": []}])))
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="[图片描述失败，视觉模型调用失败]"))
    assert exc.value.reason == "PARSE"


def test_describe_chat_non_str_content_raises_parse():
    client = _client_with(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": ["a", "b"]}}]}))
    )
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="[图片描述失败，视觉模型调用失败]"))
    assert exc.value.reason == "PARSE"


def test_describe_anthropic_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/v1/messages"
        body = json.loads(request.content)
        assert body["model"] == "qwen-vl-max"
        assert body["messages"][0]["content"][1]["type"] == "image"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "一只橘猫"}]})

    client = _anthropic_client_with(httpx.MockTransport(handler))
    assert client.describe(ImageBlock(base64="ZGF0YQ==", media_type="image/png")) == "一只橘猫"


def test_describe_anthropic_malformed_content_raises_parse():
    client = _anthropic_client_with(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"content": {"type": "text"}}))
    )
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(base64="ZGF0YQ==", media_type="image/png"))
    assert exc.value.reason == "PARSE"
