from __future__ import annotations

from qwen_mm_plugins_proxy.stream import (
    stream_same_protocol,
    translate_anthropic_to_chat,
    translate_chat_to_anthropic,
)


def test_stream_same_protocol_passthrough():
    lines = [
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}',
        "data: [DONE]",
    ]
    assert list(stream_same_protocol(lines)) == lines


def test_translate_anthropic_text_to_chat():
    lines = [
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}',
        'data: {"type":"message_stop"}',
        "data: [DONE]",
    ]
    out = list(translate_anthropic_to_chat(lines))
    assert '"choices"' in out[0] and '"delta"' in out[0] and '"content":"hi"' in out[0]
    assert out.count("data: [DONE]") == 1
    assert out[-1] == "data: [DONE]"


def test_translate_chat_text_to_anthropic():
    lines = [
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        "data: [DONE]",
    ]
    out = list(translate_chat_to_anthropic(lines))
    assert '"content_block_delta"' in out[0]
    assert '"text_delta"' in out[0] and '"text":"hi"' in out[0]
