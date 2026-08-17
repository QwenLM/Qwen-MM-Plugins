from __future__ import annotations

import base64

from qwen_mm_plugins_proxy.cache import DescriptionCache
from qwen_mm_plugins_proxy.config import ProxyConfig
from qwen_mm_plugins_proxy.ir import parse_chat
from qwen_mm_plugins_proxy.pipeline import Pipeline
from qwen_mm_plugins_proxy.vlm import VLMError

DATA_URL = "data:image/png;base64,QUJD"
DATA_URL_A = "data:image/png;base64,QUJB"
DATA_URL_B = "data:image/png;base64,QUJC"

# bytes//2 -> text_tokens=115050 -> X=1.5 -> quota=1
_BUDGET_ONE_TEXT = "x" * 230100
# text_tokens=115100 -> X=1.0 -> CONTEXT_FULL
_CONTEXT_FULL_TEXT = "x" * 230200


class FakeVLM:
    def __init__(self, text="一只橘猫", by_url=None):
        self.text = text
        self.by_url = by_url or {}
        self.calls = 0

    def describe(self, image, question=None, tier=1):
        self.calls += 1
        return self.by_url.get(getattr(image, "url", None), self.text)


def _no_image_blocks(ir) -> bool:
    def walk(blocks) -> bool:
        for b in blocks:
            if b.type == "image":
                return False
            if b.type == "tool_result" and b.tool_result_content:
                if not walk(b.tool_result_content):
                    return False
        return True

    return all(walk(m.content) for m in ir.messages)


def _all_text(ir) -> str:
    parts: list[str] = []

    def walk(blocks) -> None:
        for b in blocks:
            if b.type == "text" and b.text:
                parts.append(b.text)
            elif b.type == "tool_result" and b.tool_result_content:
                walk(b.tool_result_content)

    for m in ir.messages:
        walk(m.content)
    return "\n".join(parts)


def _ir_with_image(model="deepseek-v4-pro"):
    return parse_chat(
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": DATA_URL}},
                    ],
                }
            ],
        }
    )


def test_injects_description_and_removes_image_block():
    vlm = FakeVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    ir = _ir_with_image()
    result = pipe.process(ir, ProxyConfig())
    texts = [b.text for b in result.ir.messages[0].content if b.type == "text"]
    assert any("[图片描述]" in t and "橘猫" in t for t in texts)
    assert not any(b.type == "image" for m in result.ir.messages for b in m.content)
    assert result.injected == 1 and result.vlm_calls == 1


def test_fail_open_on_vlm_error():
    class BoomVLM:
        def describe(self, image, question=None, tier=1):
            raise VLMError("TIMEOUT", "timeout")

    pipe = Pipeline(BoomVLM(), DescriptionCache())
    result = pipe.process(_ir_with_image(), ProxyConfig())
    assert result.fail_open == "TIMEOUT"
    assert not any(b.type == "image" for m in result.ir.messages for b in m.content)
    texts = [b.text for b in result.ir.messages[0].content if b.type == "text"]
    assert any("看不到图" in t for t in texts)


def test_vision_model_passthrough_no_pipeline():
    pipe = Pipeline(FakeVLM(), DescriptionCache())
    ir = _ir_with_image(model="qwen-vl-max")  # 内置名单 vision -> 直通
    result = pipe.process(ir, ProxyConfig(model_capabilities={}))
    assert result.vlm_calls == 0 and result.injected == 0


def test_context_full_strips_all_images_without_vlm():
    vlm = FakeVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    ir = parse_chat(
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": _CONTEXT_FULL_TEXT}]},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": DATA_URL}}]},
            ],
        }
    )
    result = pipe.process(ir, ProxyConfig())
    assert result.fail_open == "CONTEXT_FULL"
    assert result.vlm_calls == 0 and result.injected == 0
    assert result.stripped == 1
    assert _no_image_blocks(result.ir)
    assert "上下文已满" in _all_text(result.ir)


def test_current_turn_multi_image_injected_even_when_quota_one():
    vlm = FakeVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    ir = parse_chat(
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _BUDGET_ONE_TEXT},
                        {"type": "image_url", "image_url": {"url": DATA_URL_A}},
                        {"type": "image_url", "image_url": {"url": DATA_URL_B}},
                    ],
                }
            ],
        }
    )
    result = pipe.process(ir, ProxyConfig())
    assert result.injected == 2
    assert result.vlm_calls == 2
    assert result.stripped == 0
    assert _no_image_blocks(result.ir)
    text = _all_text(result.ir)
    assert "[[图片1]]" in text and "[[图片2]]" in text


def test_history_quota_prefers_recent_and_strips_rest():
    vlm = FakeVLM(by_url={DATA_URL_B: "desc-B", DATA_URL_A: "desc-A"})
    pipe = Pipeline(vlm, DescriptionCache())
    ir = parse_chat(
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": DATA_URL_A}}]},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": DATA_URL_B}}]},
                {"role": "user", "content": [{"type": "text", "text": _BUDGET_ONE_TEXT}]},
            ],
        }
    )
    result = pipe.process(ir, ProxyConfig())
    assert result.injected == 1
    assert result.vlm_calls == 1
    assert result.stripped == 1
    assert _no_image_blocks(result.ir)
    text = _all_text(result.ir)
    assert "desc-B" in text  # 最近的历史图拿到配额
    assert "desc-A" not in text  # 更旧的历史图被剥离
    assert "历史预算已满" in text


def test_nested_tool_result_image_injected():
    vlm = FakeVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    ir = parse_chat(
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "user", "content": "看图"},
                {"role": "assistant", "content": [{"type": "text", "text": "好"}]},
                {
                    "role": "tool",
                    "content": [
                        {"type": "text", "text": "工具结果"},
                        {"type": "image_url", "image_url": {"url": DATA_URL}},
                    ],
                },
            ],
        }
    )
    result = pipe.process(ir, ProxyConfig())
    assert result.injected == 1
    assert result.vlm_calls == 1
    assert result.stripped == 0
    assert _no_image_blocks(result.ir)
    assert "一只橘猫" in _all_text(result.ir)


def test_text_embedded_data_url_replaced_no_base64_residue():
    vlm = FakeVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    text = f"看图 {DATA_URL} 结束"
    ir = parse_chat(
        {
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": text}],
        }
    )
    result = pipe.process(ir, ProxyConfig())
    assert result.injected == 1
    assert result.vlm_calls == 1
    assert _no_image_blocks(result.ir)
    all_text = _all_text(result.ir)
    assert "base64" not in all_text and "QUJD" not in all_text
    assert "[图片]" in all_text
    assert "一只橘猫" in all_text


def test_deep_history_cache_hit_injected_miss_stripped():
    urls = [f"data:image/png;base64,{base64.b64encode(bytes([i])).decode()}" for i in range(12)]
    cache = DescriptionCache()
    cache.put(urls[1], None, "深层缓存描述")  # m1 是深层历史，命中缓存
    pipe = Pipeline(FakeVLM(), cache)
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]} for url in urls]
    messages.append({"role": "user", "content": "看图"})  # 当前轮，无图
    ir = parse_chat({"model": "deepseek-v4-pro", "messages": messages})
    result = pipe.process(ir, ProxyConfig())
    assert result.injected == 11  # 10 条黄金窗口 + 1 条深层缓存命中
    assert result.vlm_calls == 10  # 深层命中不再调用 VLM
    assert result.stripped == 1  # m0 深层未缓存 -> 剥离
    assert _no_image_blocks(result.ir)
    text = _all_text(result.ir)
    assert "深层缓存描述" in text
    assert "深层历史未缓存" in text
