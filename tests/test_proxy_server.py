from __future__ import annotations

import json
import threading

import httpx
from qwen_mm_plugins_proxy.config import ProxyConfig, RelayConfig
from qwen_mm_plugins_proxy.pipeline import Pipeline
from qwen_mm_plugins_proxy.server import _select_relay, _upstream_url, run_server


class NoopVLM:
    def describe(self, image, question=None, tier=1):
        return "fake description"


def test_upstream_url_tolerates_with_and_without_v1():
    for base in ("http://127.0.0.1:9", "http://127.0.0.1:9/v1"):
        url = _upstream_url(RelayConfig(name="u", protocol="chat", base_url=base), "/chat/completions")
        assert url == "http://127.0.0.1:9/v1/chat/completions"


def test_upstream_url_build_versioned_url_rules():
    """按 Codex++ build_versioned_url 启发式的拼接规则（对齐其单测预期）。

    规则：纯 origin 加 /v1；已带 v<数字> 版本段 / 非版本路径 / # 结尾 → 直接拼；
    已以 path 结尾 → 原样；/v1/v1 去重。
    """
    cases = [
        # 纯 origin → 加 /v1
        ("http://127.0.0.1:9", "/chat/completions", "http://127.0.0.1:9/v1/chat/completions"),
        ("https://api.deepseek.com", "/chat/completions", "https://api.deepseek.com/v1/chat/completions"),
        # 已带 v<数字> 版本段 → 直接拼
        ("http://127.0.0.1:9/v1", "/chat/completions", "http://127.0.0.1:9/v1/chat/completions"),
        (
            "https://ark.example.com/api/coding/v3",
            "/chat/completions",
            "https://ark.example.com/api/coding/v3/chat/completions",
        ),
        ("https://ark.example.com/api/coding/v3", "/responses", "https://ark.example.com/api/coding/v3/responses"),
        ("https://api.example.com/v2", "/chat/completions", "https://api.example.com/v2/chat/completions"),
        ("https://api.example.com/v1beta", "/chat/completions", "https://api.example.com/v1beta/chat/completions"),
        # 非版本路径 → 直接拼
        ("https://api.example.com/openai", "/chat/completions", "https://api.example.com/openai/chat/completions"),
        ("https://api.deepseek.com/anthropic/v1", "/messages", "https://api.deepseek.com/anthropic/v1/messages"),
        ("https://ark.example.com/api/coding/v1", "/messages", "https://ark.example.com/api/coding/v1/messages"),
        # # 结尾 → 跳过版本前缀
        ("https://api.example.com/openai#", "/chat/completions", "https://api.example.com/openai/chat/completions"),
        # 已以 path 结尾 → 原样
        (
            "https://api.example.com/v1/chat/completions",
            "/chat/completions",
            "https://api.example.com/v1/chat/completions",
        ),
        # /v1/v1 去重
        ("https://api.example.com/v1/v1", "/chat/completions", "https://api.example.com/v1/chat/completions"),
    ]
    for base, path, expected in cases:
        url = _upstream_url(RelayConfig(name="u", protocol="chat", base_url=base), path)
        assert url == expected, f"{base} + {path} -> {url}, expected {expected}"


def test_upstream_url_anthropic_always_versions():
    """anthropic 协议：Anthropic 端点固定 /v1/messages，直连根（无版本段）也补 /v1；
    已带 /v1 时去重。"""
    cases = [
        ("https://ark.example.com/api/coding", "/messages", "https://ark.example.com/api/coding/v1/messages"),
        ("https://api.anthropic.com", "/messages", "https://api.anthropic.com/v1/messages"),
        ("https://api.deepseek.com/anthropic/v1", "/messages", "https://api.deepseek.com/anthropic/v1/messages"),
        ("https://api.example.com/v1/messages", "/messages", "https://api.example.com/v1/messages"),
    ]
    for base, path, expected in cases:
        url = _upstream_url(RelayConfig(name="u", protocol="anthropic", base_url=base), path)
        assert url == expected, f"{base} + {path} -> {url}, expected {expected}"


def test_select_relay_matches_model_then_protocol_then_default():
    cfg = ProxyConfig(
        relays=[
            RelayConfig(name="r1", protocol="chat", base_url="http://r1", models=["deepseek/*"]),
            RelayConfig(name="r2", protocol="chat", base_url="http://r2", models=["qwen-*"]),
            RelayConfig(name="r3", protocol="anthropic", base_url="http://r3"),
        ],
    )
    # model 匹配：deepseek-* 落到 r1
    assert _select_relay(cfg, "chat", "deepseek-v3").name == "r1"
    # model 匹配：qwen-* 落到 r2（同 protocol 下按 model 区分）
    assert _select_relay(cfg, "chat", "qwen-max").name == "r2"
    # 无 model 匹配 -> 回退 protocol 首条
    assert _select_relay(cfg, "chat", "glm-4").name == "r1"
    # 协议不匹配 -> 默认 relay（base_url 空）
    d = _select_relay(cfg, "responses", "deepseek-v3")
    assert d.base_url == "" and d.name == "default"
    # 空 model（旧签名）-> 回退协议
    assert _select_relay(cfg, "chat").name == "r1"


def test_proxy_forwards_text_request_and_transcribes_image(upstream):
    cfg = ProxyConfig(
        bind_port=0,  # ephemeral: avoid colliding with the default 8787 (or another test run)
        relays=[RelayConfig(name="up", protocol="chat", base_url=f"http://127.0.0.1:{upstream.port}/v1")],
        vlm=__import__("qwen_mm_plugins_proxy.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(
        NoopVLM(), __import__("qwen_mm_plugins_proxy.cache", fromlist=["DescriptionCache"]).DescriptionCache()
    )
    server = run_server(cfg)
    server.pipeline = pipe  # inject NoopVLM pipeline (brief omits this; run_server creates a real VLMClient)
    server_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server_port}/v1/chat/completions",
            json={
                "model": "deepseek-v4-pro",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看图"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                        ],
                    }
                ],
            },
        )
        assert resp.status_code == 200
        assert upstream.received
        # 上游收到的消息里图片已被替换成描述文字
        texts = json.dumps(upstream.received[-1])
        assert "fake description" in texts
        assert "base64,QUJD" not in texts
    finally:
        server.shutdown()


def test_proxy_does_not_truncate_multibyte_upstream_response(upstream):
    """中文（多字节）上游回复不得被截断。

    回归 T1-T4 手测 bug：do_POST 曾用 len(text)（字符数）当 content-length，而实际写入的
    是 UTF-8 字节；中文每字 3 字节使 content-length 偏小，客户端读到截断的多字节尾部
    （中文被掐成半句、JSON 都不完整）。必须按字节数写 content-length。
    """
    upstream.content = "北京的秋天很美。" * 500  # 足够长的多字节回复
    cfg = ProxyConfig(
        bind_port=0,
        relays=[RelayConfig(name="up", protocol="chat", base_url=f"http://127.0.0.1:{upstream.port}/v1")],
        vlm=__import__("qwen_mm_plugins_proxy.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(
        NoopVLM(), __import__("qwen_mm_plugins_proxy.cache", fromlist=["DescriptionCache"]).DescriptionCache()
    )
    server = run_server(cfg)
    server.pipeline = pipe
    server_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server_port}/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        )
        # JSON 能完整解析（没有被按错误的 content-length 掐断）、内容一字不缺
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "北京的秋天很美。" * 500
    finally:
        server.shutdown()


def test_upstream_url_codex_plus_local_proxy():
    # Codex++ 本地协议代理（protocol_proxy.rs，端口 57321，暴露 /v1，认 responses + chat）
    cases = [
        ("http://127.0.0.1:57321/v1", "chat", "/chat/completions", "http://127.0.0.1:57321/v1/chat/completions"),
        ("http://127.0.0.1:57321/v1", "responses", "/responses", "http://127.0.0.1:57321/v1/responses"),
        ("http://127.0.0.1:57321", "responses", "/responses", "http://127.0.0.1:57321/v1/responses"),
        ("http://127.0.0.1:57321", "chat", "/chat/completions", "http://127.0.0.1:57321/v1/chat/completions"),
    ]
    for base, proto, path, expected in cases:
        assert _upstream_url(RelayConfig(name="u", protocol=proto, base_url=base), path) == expected


def test_upstream_url_cc_switch_proxy():
    # CC Switch 本地反向代理（默认 15721），各工具用专属路径前缀 + /v1
    cases = [
        ("http://127.0.0.1:15721", "anthropic", "/messages", "http://127.0.0.1:15721/v1/messages"),
        ("http://127.0.0.1:15721", "chat", "/chat/completions", "http://127.0.0.1:15721/v1/chat/completions"),
        (
            "http://127.0.0.1:15721/grokbuild/v1",
            "chat",
            "/chat/completions",
            "http://127.0.0.1:15721/grokbuild/v1/chat/completions",
        ),
        ("http://127.0.0.1:15721/codex/v1", "responses", "/responses", "http://127.0.0.1:15721/codex/v1/responses"),
    ]
    for base, proto, path, expected in cases:
        assert _upstream_url(RelayConfig(name="u", protocol=proto, base_url=base), path) == expected


def test_proxy_two_layer_responses_preserves_model_and_strips_image(upstream):
    # 两层路由（我们代理→回环工具）responses 协议下的协议保真：
    # model 保留、图片剥离、协议形态（responses input）不变——工具能正常读模型
    cfg = ProxyConfig(
        bind_port=0,
        relays=[RelayConfig(name="tool", protocol="responses", base_url=f"http://127.0.0.1:{upstream.port}")],
        vlm=__import__("qwen_mm_plugins_proxy.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(
        NoopVLM(), __import__("qwen_mm_plugins_proxy.cache", fromlist=["DescriptionCache"]).DescriptionCache()
    )
    server = run_server(cfg)
    server.pipeline = pipe
    server_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.Client(trust_env=False).post(
            f"http://127.0.0.1:{server_port}/v1/responses",
            json={
                "model": "kimi-k2.7",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "看"},
                            {"type": "input_image", "image_url": "data:image/png;base64,QQ=="},
                        ],
                    },
                ],
            },
        )
        assert resp.status_code == 200
        sent = upstream.received[-1]
        assert sent["model"] == "kimi-k2.7"  # model 保留
        assert sent.get("input") is not None  # 同协议（responses input）形态不变
        blob = json.dumps(sent)
        assert "fake description" in blob  # 图片被转写注入
        assert "QQ==" not in blob and "data:image" not in blob  # 图不再外泄
    finally:
        server.shutdown()
