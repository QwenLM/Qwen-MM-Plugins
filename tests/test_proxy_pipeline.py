from __future__ import annotations

import base64

import pytest
from qwen_mm_plugins_proxy.cache import DescriptionCache
from qwen_mm_plugins_proxy.config import ProxyConfig
from qwen_mm_plugins_proxy.ir import parse_chat
from qwen_mm_plugins_proxy.pipeline import (
    VLM_BACKOFF_BASE,
    VLM_BACKOFF_JITTER,
    VLM_MAX_ATTEMPTS,
    Pipeline,
    ProcessResult,
)
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


def test_current_round_uses_tier2_with_question():
    """spec §5.4：当前轮带用户问题时调 Tier2 聚焦（question 传入、tier=2）。"""

    class TierVLM:
        def __init__(self):
            self.calls = []

        def describe(self, image, question=None, tier=1):
            self.calls.append((tier, question))
            return "desc"

    vlm = TierVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    ir = parse_chat(
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "红字说了什么"},
                        {"type": "image_url", "image_url": {"url": DATA_URL_A}},
                    ],
                }
            ],
        }
    )
    pipe.process(ir, ProxyConfig())
    assert vlm.calls == [(2, "红字说了什么")]


def test_current_round_without_question_uses_tier1():
    """spec §5.4：当前轮无用户文本问题 -> Tier1 全面描述（question=None）。"""

    class TierVLM:
        def __init__(self):
            self.calls = []

        def describe(self, image, question=None, tier=1):
            self.calls.append((tier, question))
            return "desc"

    vlm = TierVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    ir = parse_chat(
        {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": DATA_URL_A}}]},
            ],
        }
    )
    pipe.process(ir, ProxyConfig())
    assert vlm.calls == [(1, None)]


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


class FlakyVLM:
    """前 failures 次 describe 抛给定 VLMError，之后成功；记录调用次数。"""

    def __init__(self, failures: int, error: VLMError):
        self.failures = failures
        self.error = error
        self.calls = 0

    def describe(self, image, question=None, tier=1):  # noqa: ARG002
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "fake description"


def _retry_target():
    from qwen_mm_plugins_proxy.ir import ContentBlock, ImageBlock, Message
    from qwen_mm_plugins_proxy.pipeline import _ImageTarget

    msg = Message(
        role="user",
        content=[ContentBlock(type="image", image=ImageBlock(base64="x", media_type="image/png"))],
    )
    return _ImageTarget(msg=msg, image=msg.content[0].image, path=(0,), index=0, msg_count=1)


def test_vlm_transient_error_retries_then_succeeds():
    vlm = FlakyVLM(1, VLMError("TRANSPORT", "boom"))
    result = ProcessResult(ir=None)
    desc = Pipeline(vlm, DescriptionCache())._describe_with_retry(_retry_target().image, None, 1, result)
    assert desc == "fake description"
    assert vlm.calls == 2  # 首次失败 + 1 次重试成功
    assert result.vlm_calls == 1  # 只有成功那次计入


@pytest.mark.parametrize("reason", ["TRANSPORT", "TIMEOUT", "RATE_LIMIT", "HTTP"])
def test_vlm_retryable_reasons_repeat_then_fail_open(monkeypatch, reason):
    monkeypatch.setattr("qwen_mm_plugins_proxy.pipeline.time.sleep", lambda _s: None)
    vlm = FlakyVLM(99, VLMError(reason, "boom"))
    result = ProcessResult(ir=None)
    outcome = Pipeline(vlm, DescriptionCache())._handle_one(_retry_target(), result, None)
    assert outcome == "stripped"
    assert result.fail_open == reason
    assert vlm.calls == VLM_MAX_ATTEMPTS  # 首次 + 2 次重试，耗尽


def test_vlm_auth_does_not_retry(monkeypatch):
    monkeypatch.setattr("qwen_mm_plugins_proxy.pipeline.time.sleep", lambda _s: None)
    vlm = FlakyVLM(99, VLMError("AUTH", "bad key"))
    result = ProcessResult(ir=None)
    outcome = Pipeline(vlm, DescriptionCache())._handle_one(_retry_target(), result, None)
    assert outcome == "stripped"
    assert vlm.calls == 1  # AUTH 不可重试，只调 1 次


def test_backoff_bounds():
    for attempt in range(2):
        value = Pipeline._backoff(attempt)
        base = VLM_BACKOFF_BASE * (2**attempt)
        assert base <= value < base + VLM_BACKOFF_JITTER


def test_tool_data_url_not_counted_as_text_budget():
    """字符串内嵌 data URL 的 base64 不计入文本预算（T3/T4 场景），避免 CONTEXT_FULL。"""
    from qwen_mm_plugins_proxy.ir import parse_responses

    big = "data:image/png;base64," + "A" * 500_000  # 巨大 data URL（T3 function_call_output）
    body = {
        "model": "deepseek-v4-pro",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "check"}]},
            {"type": "function_call_output", "call_id": "c1", "output": big},
        ],
    }
    ir = parse_responses(body)
    budget = Pipeline._budget(ir, ProxyConfig())
    assert budget > 1  # 不应被判成 CONTEXT_FULL


def test_text_with_images_early_strips_data_url():
    """字符串 data URL 提前剥离：text 不含 base64、含 [图片] 占位，base64 只进 image block。"""
    from qwen_mm_plugins_proxy.ir import _text_with_images

    blocks = _text_with_images(f"前缀 {DATA_URL} 后缀")
    assert blocks[0].type == "text"
    assert "base64" not in blocks[0].text and "QUJD" not in blocks[0].text
    assert "[图片]" in blocks[0].text
    images = [b for b in blocks if b.type == "image"]
    assert len(images) == 1
    assert images[0].image.url == DATA_URL


def test_regular_url_not_stripped():
    """普通网址 / 文件路径不被误剥（正则只匹配 data:image/...;base64,）。"""
    from qwen_mm_plugins_proxy.ir import _text_with_images

    text = "看这个 https://example.com/img.png 和 " + r"C:\tmp\x.png"
    blocks = _text_with_images(text)
    assert len(blocks) == 1 and blocks[0].type == "text"
    assert blocks[0].text == text


def test_assistant_message_image_collected():
    """assistant 消息里的图片块也被收集处理（Claude Code/Codex 模型主动调工具返回图）。"""
    from qwen_mm_plugins_proxy.ir import ContentBlock, ImageBlock, IRRequest, Message

    msg = Message(
        role="assistant",
        content=[ContentBlock(type="image", image=ImageBlock(base64="x", media_type="image/png"))],
    )
    ir = IRRequest(
        model="deepseek-v4-pro",
        messages=[msg, Message(role="user", content=[ContentBlock(type="text", text="hi")])],
    )
    collected = Pipeline(FakeVLM(), DescriptionCache())._collect_images(ir)
    assert any(m.role == "assistant" for m, _ in collected)


def test_followup_injected_when_history_image_and_text_question():
    """spec §5.8：历史有图 + 当前轮纯文本追问 → 注入防编造提示。"""
    from qwen_mm_plugins_proxy.ir import ContentBlock, ImageBlock, IRRequest, Message

    history = Message(
        role="user", content=[ContentBlock(type="image", image=ImageBlock(base64="x", media_type="image/png"))]
    )
    current = Message(role="user", content=[ContentBlock(type="text", text="红色文字说了什么")])
    ir = IRRequest(model="deepseek-v4-pro", messages=[history, current])
    result = Pipeline(FakeVLM(), DescriptionCache()).process(ir, ProxyConfig())
    assert "需要重新查看原始图片" in _all_text(result.ir)


def test_followup_not_injected_no_history_image():
    from qwen_mm_plugins_proxy.ir import ContentBlock, IRRequest, Message

    current = Message(role="user", content=[ContentBlock(type="text", text="你好")])
    ir = IRRequest(model="deepseek-v4-pro", messages=[current])
    result = Pipeline(FakeVLM(), DescriptionCache()).process(ir, ProxyConfig())
    assert "需要重新查看原始图片" not in _all_text(result.ir)


def test_followup_not_injected_current_turn_has_image():
    from qwen_mm_plugins_proxy.ir import ContentBlock, ImageBlock, IRRequest, Message

    history = Message(
        role="user", content=[ContentBlock(type="image", image=ImageBlock(base64="x", media_type="image/png"))]
    )
    current = Message(
        role="user", content=[ContentBlock(type="image", image=ImageBlock(base64="y", media_type="image/png"))]
    )
    ir = IRRequest(model="deepseek-v4-pro", messages=[history, current])
    result = Pipeline(FakeVLM(), DescriptionCache()).process(ir, ProxyConfig())
    assert "需要重新查看原始图片" not in _all_text(result.ir)
