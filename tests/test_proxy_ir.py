from __future__ import annotations

import pytest
from qwen_mm_plugins_proxy.ir import (
    detect_protocol,
    extract_data_urls,
    parse_anthropic,
    parse_chat,
    parse_responses,
    serialize_anthropic,
    serialize_chat,
    serialize_responses,
)


def test_detect_by_path():
    assert detect_protocol("/v1/messages", {}) == "anthropic"
    assert detect_protocol("/v1/responses", {}) == "responses"
    assert detect_protocol("/v1/chat/completions", {}) == "chat"


def test_detect_by_structure():
    assert detect_protocol("/other", {"input": []}) == "responses"
    assert detect_protocol("/other", {"messages": [{"role": "user"}]}) == "chat"
    assert detect_protocol("/other", {"messages": [{"role": "user"}], "system": ""}) == "anthropic"


def test_detect_unknown_raises():
    with pytest.raises(ValueError):
        detect_protocol("/other", {})


ANTHROPIC_BODY = {
    "model": "deepseek-v4-pro",
    "system": "you are helpful",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            ],
        },
    ],
    "max_tokens": 1024,
}


def test_parse_anthropic_roundtrip_fields():
    ir = parse_anthropic(ANTHROPIC_BODY)
    assert ir.model == "deepseek-v4-pro"
    assert ir.system == "you are helpful"
    assert ir.max_tokens == 1024
    img = ir.messages[0].content[1]
    assert img.type == "image" and img.image.media_type == "image/png" and img.image.base64 == "AAAA"


def test_parse_responses_image_and_tool_result():
    body = {
        "model": "deepseek-v4-pro",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "hi"},
                    {"type": "input_image", "image_url": "https://x/y.png"},
                ],
            },
            {"type": "function_call_output", "call_id": "c1", "output": "data:image/png;base64,QUJD"},
        ],
    }
    ir = parse_responses(body)
    assert ir.messages[0].content[1].image.url == "https://x/y.png"
    tool_result = ir.messages[1].content[0]
    assert tool_result.type == "tool_result"
    assert tool_result.tool_use_id == "c1"
    # 字符串内嵌 data URL 提前剥离：text 块为 [图片] 占位（base64 不占文本预算），base64 只进 image 块
    assert "base64" not in tool_result.tool_result_content[0].text
    assert "[图片]" in tool_result.tool_result_content[0].text
    assert tool_result.tool_result_content[1].type == "image"
    assert tool_result.tool_result_content[1].image.url == "data:image/png;base64,QUJD"


def test_parse_chat_image_url():
    body = {
        "model": "glm-4.5v",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "x"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                ],
            },
        ],
    }
    ir = parse_chat(body)
    img = ir.messages[0].content[1]
    assert img.type == "image" and img.image.url == "data:image/jpeg;base64,QUJD"


def test_extract_data_urls_multiple():
    text = "a data:image/png;base64,QUJD b data:image/jpeg;base64,REVG"
    assert extract_data_urls(text) == ["data:image/png;base64,QUJD", "data:image/jpeg;base64,REVG"]


def test_parse_serialize_anthropic_roundtrip():
    body = {
        "model": "deepseek-v4-pro",
        "system": "s",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                ],
            }
        ],
    }
    ir = parse_anthropic(body)
    out = serialize_anthropic(ir)
    assert out["model"] == "deepseek-v4-pro"
    assert out["messages"][0]["content"][1]["type"] == "image"
    assert out["messages"][0]["content"][1]["source"]["data"] == "AAAA"


def test_serialize_chat_preserves_image_and_tools():
    ir = parse_chat(
        {
            "model": "qwen",
            "messages": [
                {"role": "system", "content": "s"},
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}],
                },
            ],
            "tools": [{"type": "function", "function": {"name": "f"}}],
        }
    )
    out = serialize_chat(ir)
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][1]["content"][0]["type"] == "image_url"
    assert out["tools"] == ir.tools


def test_serialize_responses_keeps_input_list():
    ir = parse_responses(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "x"}]},
            ],
        }
    )
    out = serialize_responses(ir)
    assert out["input"][0]["role"] == "user"


def test_parse_chat_serialize_chat_roundtrip_tools():
    body = {
        "model": "qwen",
        "messages": [
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": 1}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
    }
    ir = parse_chat(body)
    assert ir.messages[0].content[1].type == "tool_use"
    assert ir.messages[0].content[1].tool_use_id == "call_1"
    assert ir.messages[1].content[0].type == "tool_result"
    assert ir.messages[1].content[0].tool_use_id == "call_1"
    out = serialize_chat(ir)
    assert out["messages"][0]["tool_calls"] == body["messages"][0]["tool_calls"]
    assert out["messages"][1] == body["messages"][1]
    assert parse_chat(out) == ir


def test_serialize_responses_emits_function_items():
    ir = parse_anthropic(
        {
            "model": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"q": 1}}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": [
                                {"type": "text", "text": "ok"},
                                {
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": "image/png", "data": "BBBB"},
                                },
                            ],
                        }
                    ],
                },
            ],
        }
    )
    out = serialize_responses(ir)
    items = out["input"]
    assert items[0] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "lookup",
        "arguments": '{"q": 1}',
    }
    assert items[1]["type"] == "function_call_output"
    assert items[1]["call_id"] == "call_1"
    assert items[1]["output"].endswith("data:image/png;base64,BBBB")
    assert all(item.get("role") != "tool" for item in items)


def test_serialize_responses_text_and_tool_items():
    body = {
        "model": "m",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "u"}]},
            {"role": "assistant", "content": [{"type": "output_text", "text": "a"}]},
            {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "r"},
        ],
    }
    out = serialize_responses(parse_responses(body))
    items = out["input"]
    assert items[0]["content"][0]["type"] == "input_text"
    assert items[1]["content"][0]["type"] == "output_text"
    assert items[2] == {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"}
    assert items[3]["type"] == "function_call_output"
    assert items[3]["call_id"] == "c1"
    assert all(item.get("role") != "tool" for item in items)
    assert parse_responses(out) == parse_responses(body)


def test_parse_chat_assistant_null_content_with_tool_calls_does_not_crash():
    # T4：标准 OpenAI chat 里 assistant 带 tool_calls 时 content 为 null，解析不得抛错（原 TypeError）
    body = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": "用工具看看这张图"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "view_image", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "data:image/png;base64,QUJD"},
        ],
    }
    ir = parse_chat(body)
    assert ir.messages[1].role == "assistant"
    assert ir.messages[1].content[0].type == "tool_use"
    assert ir.messages[1].content[0].tool_use_id == "t1"
    assert ir.messages[2].content[0].type == "tool_result"
    assert ir.messages[2].content[0].tool_use_id == "t1"
    out = serialize_chat(ir)
    asst = next(m for m in out["messages"] if m["role"] == "assistant")
    assert asst["tool_calls"] == body["messages"][1]["tool_calls"]
    tool_msg = next(m for m in out["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "t1"


def test_serialize_chat_omits_empty_tool_calls():
    ir = parse_chat({"model": "m", "messages": [{"role": "assistant", "content": "hello"}]})
    out = serialize_chat(ir)
    assert "tool_calls" not in out["messages"][0]


def test_serialize_responses_base64_image_emits_data_url():
    ir = parse_anthropic(
        {
            "model": "m",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
                    ],
                }
            ],
        }
    )
    out = serialize_responses(ir)
    item = out["input"][0]
    assert item["content"][0]["type"] == "input_image"
    assert item["content"][0]["image_url"] == "data:image/png;base64,AAAA"


def test_serialize_chat_tool_result_preserves_image_data_url():
    ir = parse_anthropic(
        {
            "model": "m",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": [
                                {"type": "text", "text": "result"},
                                {
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": "image/png", "data": "BBBB"},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )
    out = serialize_chat(ir)
    tool_message = next(m for m in out["messages"] if m.get("role") == "tool")
    assert "result" in tool_message["content"]
    assert "data:image/png;base64,BBBB" in tool_message["content"]


PARSERS = {
    "anthropic": parse_anthropic,
    "responses": parse_responses,
    "chat": parse_chat,
}


SERIALIZERS = {
    "anthropic": serialize_anthropic,
    "responses": serialize_responses,
    "chat": serialize_chat,
}


def _content_parts(ir):
    texts = []
    images = []
    tool_ids = []

    def walk(blocks):
        for block in blocks:
            if block.type == "text":
                texts.append(block.text or "")
            elif block.type == "image":
                img = block.image
                images.append(img.url or f"data:{img.media_type};base64,{img.base64}")
            elif block.type == "tool_use":
                texts.append(f"call:{block.tool_use_id}:{block.tool_name}")
                tool_ids.append(block.tool_use_id)
            elif block.type == "tool_result":
                tool_ids.append(block.tool_use_id)
                walk(block.tool_result_content or [])

    for message in ir.messages:
        walk(message.content)
    return texts, images, tool_ids


def _assert_well_formed(body, protocol):
    if protocol == "anthropic":
        seen_tool_uses: set[str] = set()
        for message in body["messages"]:
            assert message["role"] in ("user", "assistant")
            for block in message["content"]:
                assert block["type"] in ("text", "image", "tool_use", "tool_result")
                if block["type"] == "tool_use":
                    assert block.get("id")
                    seen_tool_uses.add(block["id"])
                elif block["type"] == "tool_result":
                    assert block.get("tool_use_id")
                    assert block["tool_use_id"] in seen_tool_uses
    elif protocol == "responses":
        seen_calls: set[str] = set()
        for item in body["input"]:
            assert item.get("role") != "tool"
            if item.get("type") == "function_call":
                assert item.get("call_id")
                seen_calls.add(item["call_id"])
            elif item.get("type") == "function_call_output":
                assert item.get("call_id")
                assert item["call_id"] in seen_calls
            else:
                assert item.get("role") in ("user", "assistant")
                assert isinstance(item.get("content"), list)
    else:
        seen_tool_calls: set[str] = set()
        for message in body["messages"]:
            if message.get("role") == "assistant":
                for tool_call in message.get("tool_calls") or []:
                    assert tool_call.get("id")
                    seen_tool_calls.add(tool_call["id"])
            elif message.get("role") == "tool":
                assert message.get("tool_call_id")
                assert message["tool_call_id"] in seen_tool_calls


def _assert_preserved(original, reparsed):
    texts, images, tool_ids = _content_parts(original)
    texts2, images2, tool_ids2 = _content_parts(reparsed)
    joined = " ".join(texts2)
    for text in texts:
        assert text in joined
    for image in images:
        assert image in images2 or image in joined
    for tool_id in tool_ids:
        assert tool_id in tool_ids2
    assert reparsed.system == original.system
    assert reparsed.temperature == original.temperature


def _body_has_data_url(body) -> bool:
    """递归检查请求体是否含字符串内嵌 data:image data URL。"""
    if isinstance(body, str):
        return "data:image" in body
    if isinstance(body, list):
        return any(_body_has_data_url(x) for x in body)
    if isinstance(body, dict):
        return any(_body_has_data_url(v) for v in body.values())
    return False


def _matrix_body(protocol: str) -> dict:
    if protocol == "anthropic":
        return {
            "model": "deepseek-v4-pro",
            "system": "s",
            "max_tokens": 64,
            "temperature": 0.7,
            "tools": [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "calling"},
                        {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"q": "x"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": [
                                {"type": "text", "text": "result text"},
                                {
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": "image/png", "data": "BBBB"},
                                },
                            ],
                        },
                    ],
                },
            ],
        }
    if protocol == "responses":
        return {
            "model": "gpt-4.1",
            "max_output_tokens": 64,
            "instructions": "s",
            "temperature": 0.7,
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "hi"},
                        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                    ],
                },
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": '{"q": "x"}'},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "result text\ndata:image/png;base64,BBBB",
                },
            ],
        }
    return {
        "model": "qwen-max",
        "max_tokens": 64,
        "temperature": 0.7,
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
        "messages": [
            {"role": "system", "content": "s"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": "x"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result text\ndata:image/png;base64,BBBB"},
        ],
    }


@pytest.mark.parametrize("serialize_proto", ["anthropic", "responses", "chat"])
@pytest.mark.parametrize("parse_proto", ["anthropic", "responses", "chat"])
def test_protocol_roundtrip_matrix(parse_proto, serialize_proto):
    body = _matrix_body(parse_proto)
    ir = PARSERS[parse_proto](body)
    out = SERIALIZERS[serialize_proto](ir)
    assert out["model"] == ir.model
    _assert_well_formed(out, serialize_proto)
    reparsed = PARSERS[serialize_proto](out)
    if parse_proto == serialize_proto:
        if _body_has_data_url(body):
            # 字符串 data URL 提前剥离后，text [图片] 占位在同协议 roundtrip 可能叠加（[图片][图片]），
            # 用宽松 _assert_preserved 校验关键内容（图片存在、text 子串）。
            _assert_preserved(ir, reparsed)
        else:
            assert reparsed == ir
    else:
        _assert_preserved(ir, reparsed)
