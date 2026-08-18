"""Unified intermediate representation (IR) + inbound protocol detection.

All three inbound protocols normalize into IRRequest; the safety net works only on IR
(spec §3: one image pipeline for three protocols).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class ImageBlock:
    """Normalized image: either url (OpenAI forms) or base64+media_type (Anthropic)."""

    url: str | None = None
    media_type: str | None = None
    base64: str | None = None


@dataclass
class ContentBlock:
    type: str  # text | image | tool_use | tool_result
    text: str | None = None
    image: ImageBlock | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_result_content: list["ContentBlock"] | None = None


@dataclass
class Message:
    role: str  # user | assistant | tool
    content: list[ContentBlock] = field(default_factory=list)


@dataclass
class IRRequest:
    model: str
    messages: list[Message]
    system: str | None = None
    tools: list[dict] | None = None
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


def detect_protocol(path: str, body: dict) -> str:
    """Inbound protocol by request path first, then by body structure. Raise on ambiguity."""
    if path.endswith("/v1/messages") or "/messages" in path:
        return "anthropic"
    if path.endswith("/v1/responses") or "/responses" in path:
        return "responses"
    if path.endswith("/v1/chat/completions") or "/chat/completions" in path:
        return "chat"
    # Structure fallback (relay-converted paths).
    if "input" in body:
        return "responses"
    if "messages" in body:
        if "system" in body or any(
            m.get("role") == "assistant" and "content" in m and isinstance(m["content"], list) for m in body["messages"]
        ):
            return "anthropic"
        return "chat"
    raise ValueError(f"cannot detect protocol for path={path!r}")


_DATA_URL_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def extract_data_urls(text: str) -> list[str]:
    """Pull string-embedded base64 data URLs out of tool/assistant text (spec §4.2.1)."""
    return _DATA_URL_RE.findall(text)


def _image_block(source: dict) -> ContentBlock:
    if source.get("type") == "base64":
        return ContentBlock(
            type="image", image=ImageBlock(media_type=source.get("media_type"), base64=source.get("data"))
        )
    return ContentBlock(type="image", image=ImageBlock(url=source.get("url")))


def _text_with_images(text: str) -> list[ContentBlock]:
    """A text block followed by one image block per embedded data URL (spec §4.2.1).

    data URL 在文本层提前剥离为 [图片] 占位：base64 只进 image block，不占文本预算、
    不碰主模型文本；普通网址/文件路径/非 image data URL 不匹配、原样保留。
    """
    urls = extract_data_urls(text)
    if not urls:
        return [ContentBlock(type="text", text=text)]
    cleaned = text
    for url in urls:
        cleaned = cleaned.replace(url, "[图片]", 1)
    blocks = [ContentBlock(type="text", text=cleaned)]
    blocks.extend(ContentBlock(type="image", image=ImageBlock(url=url)) for url in urls)
    return blocks


def _content_blocks(blocks: list) -> list[ContentBlock]:
    out: list[ContentBlock] = []
    for b in blocks:
        kind = b.get("type")
        if kind in (None, "text", "input_text", "output_text"):
            out.append(ContentBlock(type="text", text=b.get("text", "")))
        elif kind in ("image", "input_image", "image_url"):
            if "source" in b:
                out.append(_image_block(b["source"]))
            else:
                raw = b.get("image_url", {})
                url = raw.get("url") if isinstance(raw, dict) else raw
                out.append(ContentBlock(type="image", image=ImageBlock(url=url)))
        elif kind == "tool_use":
            out.append(
                ContentBlock(
                    type="tool_use", tool_use_id=b.get("id"), tool_name=b.get("name"), tool_input=b.get("input")
                )
            )
        elif kind == "tool_result":
            content = b.get("content")
            if isinstance(content, str):
                out.extend(_text_with_images(content))
            else:
                out.append(
                    ContentBlock(
                        type="tool_result",
                        tool_use_id=b.get("tool_use_id"),
                        tool_result_content=_content_blocks(content or []),
                    )
                )
        elif kind == "function_call":
            out.append(
                ContentBlock(
                    type="tool_use",
                    tool_use_id=b.get("call_id"),
                    tool_name=b.get("name"),
                    tool_input=json.loads(b.get("arguments") or "{}"),
                )
            )
        elif kind == "function_call_output":
            out.append(
                ContentBlock(
                    type="tool_result",
                    tool_use_id=b.get("call_id"),
                    tool_result_content=_text_with_images(str(b.get("output") or "")),
                )
            )
        else:
            out.append(ContentBlock(type="text", text=str(b)))
    return out


def _message(role: str, content) -> Message:
    if content is None:
        # 兼容标准 OpenAI chat 里 assistant 带 tool_calls 时 content 为 null 的写法
        return Message(role=role, content=[])
    if isinstance(content, str):
        return Message(role=role, content=_text_with_images(content))
    return Message(role=role, content=_content_blocks(content))


def parse_anthropic(body: dict) -> IRRequest:
    return IRRequest(
        model=body.get("model", ""),
        system=body.get("system"),
        messages=[_message(m["role"], m["content"]) for m in body.get("messages", [])],
        tools=body.get("tools"),
        stream=bool(body.get("stream")),
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
    )


def parse_responses(body: dict) -> IRRequest:
    messages: list[Message] = []
    for item in body.get("input", []):
        role = item.get("role")
        if role == "user":
            messages.append(_message("user", item.get("content", [])))
        elif role == "assistant":
            messages.append(_message("assistant", item.get("content", [])))
        elif item.get("type") in ("function_call", "function_call_output"):
            messages.append(_message("tool", [item]))
    return IRRequest(
        model=body.get("model", ""),
        messages=messages,
        tools=body.get("tools"),
        stream=bool(body.get("stream")),
        max_tokens=body.get("max_output_tokens"),
        system=body.get("instructions"),
        temperature=body.get("temperature"),
    )


def _chat_message(message: dict) -> Message:
    role = message["role"]
    content = message.get("content", "")
    if role == "assistant":
        blocks = _message("assistant", content).content
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            blocks.append(
                ContentBlock(
                    type="tool_use",
                    tool_use_id=tool_call.get("id"),
                    tool_name=fn.get("name"),
                    tool_input=json.loads(fn.get("arguments") or "{}"),
                )
            )
        return Message(role="assistant", content=blocks)
    if role == "tool":
        return Message(
            role="tool",
            content=[
                ContentBlock(
                    type="tool_result",
                    tool_use_id=message.get("tool_call_id"),
                    tool_result_content=_message("tool", content).content,
                )
            ],
        )
    return _message(role, content)


def parse_chat(body: dict) -> IRRequest:
    messages = [_chat_message(m) for m in body.get("messages", [])]
    system = None
    if messages and messages[0].role == "system":
        system = messages.pop(0).content[0].text
    return IRRequest(
        model=body.get("model", ""),
        messages=messages,
        system=system,
        tools=body.get("tools"),
        stream=bool(body.get("stream")),
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
    )


def _image_data_url(img: ImageBlock | None) -> str:
    if img is None:
        return ""
    if img.url:
        return img.url
    return f"data:{img.media_type};base64,{img.base64}"


def _block_to_proto(block: ContentBlock, protocol: str, role: str | None = None) -> dict:
    if block.type == "text":
        if protocol == "responses":
            return {"type": "output_text" if role == "assistant" else "input_text", "text": block.text}
        return {"type": "text", "text": block.text}
    if block.type == "image":
        img = block.image
        if protocol == "anthropic":
            if img and img.base64:
                return {"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.base64}}
            return {"type": "image", "source": {"type": "url", "url": _image_data_url(img)}}
        if protocol == "responses":
            return {"type": "input_image", "image_url": _image_data_url(img)}
        return {"type": "image_url", "image_url": {"url": _image_data_url(img)}}
    if block.type == "tool_use":
        if protocol == "responses":
            return {
                "type": "function_call",
                "call_id": block.tool_use_id,
                "name": block.tool_name,
                "arguments": json.dumps(block.tool_input or {}),
            }
        return {"type": "tool_use", "id": block.tool_use_id, "name": block.tool_name, "input": block.tool_input or {}}
    if block.type == "tool_result":
        if protocol == "responses":
            return {
                "type": "function_call_output",
                "call_id": block.tool_use_id,
                "output": _blocks_to_text(block.tool_result_content or []),
            }
        if protocol == "chat":
            return {
                "role": "tool",
                "tool_call_id": block.tool_use_id,
                "content": _blocks_to_text(block.tool_result_content or []),
            }
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": [_block_to_proto(b, protocol, role) for b in (block.tool_result_content or [])],
        }
    return {"type": "text", "text": ""}


def _blocks_to_text(blocks: list[ContentBlock] | None) -> str:
    parts: list[str] = []
    for block in blocks or []:
        if block.type == "text":
            parts.append(block.text or "")
        elif block.type == "image":
            data_url = _image_data_url(block.image)
            if data_url and data_url not in "\n".join(parts):
                parts.append(data_url)
    return "\n".join(parts)


def _tool_use_to_chat(block: ContentBlock) -> dict:
    return {
        "id": block.tool_use_id,
        "type": "function",
        "function": {"name": block.tool_name, "arguments": json.dumps(block.tool_input or {})},
    }


def _serialize_anthropic_messages(messages: list[Message]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            tool_use = [b for b in msg.content if b.type == "tool_use"]
            if tool_use:
                out.append({"role": "assistant", "content": [_block_to_proto(b, "anthropic") for b in tool_use]})
            rest = [b for b in msg.content if b.type != "tool_use"]
            if rest:
                out.append({"role": "user", "content": [_block_to_proto(b, "anthropic") for b in rest]})
            continue
        out.append({"role": msg.role, "content": [_block_to_proto(b, "anthropic", msg.role) for b in msg.content]})
    return out


def _serialize_responses_items(messages: list[Message]) -> list[dict]:
    items: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            plain: list[ContentBlock] = []
            for block in msg.content:
                if block.type == "tool_use":
                    if plain:
                        items.append(
                            {"type": "function_call_output", "call_id": None, "output": _blocks_to_text(plain)}
                        )
                        plain = []
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": block.tool_use_id,
                            "name": block.tool_name,
                            "arguments": json.dumps(block.tool_input or {}),
                        }
                    )
                elif block.type == "tool_result":
                    if plain:
                        items.append(
                            {"type": "function_call_output", "call_id": None, "output": _blocks_to_text(plain)}
                        )
                        plain = []
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": block.tool_use_id,
                            "output": _blocks_to_text(block.tool_result_content or []),
                        }
                    )
                else:
                    plain.append(block)
            if plain:
                items.append({"type": "function_call_output", "call_id": None, "output": _blocks_to_text(plain)})
            continue
        inline: list[dict] = []
        for block in msg.content:
            if block.type == "tool_use":
                items.append(
                    {
                        "type": "function_call",
                        "call_id": block.tool_use_id,
                        "name": block.tool_name,
                        "arguments": json.dumps(block.tool_input or {}),
                    }
                )
            elif block.type == "tool_result":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.tool_use_id,
                        "output": _blocks_to_text(block.tool_result_content or []),
                    }
                )
            else:
                inline.append(_block_to_proto(block, "responses", msg.role))
        if inline:
            items.append({"role": msg.role, "content": inline})
    return items


def _serialize_chat_messages(messages: list[Message]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            plain: list[ContentBlock] = []
            for block in msg.content:
                if block.type == "tool_use":
                    if plain:
                        out.append({"role": "tool", "tool_call_id": None, "content": _blocks_to_text(plain)})
                        plain = []
                    out.append({"role": "assistant", "content": "", "tool_calls": [_tool_use_to_chat(block)]})
                elif block.type == "tool_result":
                    if plain:
                        out.append({"role": "tool", "tool_call_id": None, "content": _blocks_to_text(plain)})
                        plain = []
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.tool_use_id,
                            "content": _blocks_to_text(block.tool_result_content or []),
                        }
                    )
                else:
                    plain.append(block)
            if plain:
                out.append({"role": "tool", "tool_call_id": None, "content": _blocks_to_text(plain)})
            continue
        inline: list[ContentBlock] = []
        tool_results: list[dict] = []
        for block in msg.content:
            if block.type == "tool_result":
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.tool_use_id,
                        "content": _blocks_to_text(block.tool_result_content or []),
                    }
                )
            else:
                inline.append(block)
        if msg.role == "assistant":
            parts: list[str] = []
            tool_calls: list[dict] = []
            for block in inline:
                if block.type == "tool_use":
                    tool_calls.append(_tool_use_to_chat(block))
                elif block.type == "text":
                    parts.append(block.text or "")
                elif block.type == "image":
                    parts.append(_image_data_url(block.image))
            message: dict = {"role": "assistant", "content": "".join(parts)}
            if tool_calls:
                message["tool_calls"] = tool_calls
            out.append(message)
        else:
            out.append({"role": msg.role, "content": [_block_to_proto(b, "chat", msg.role) for b in inline]})
        out.extend(tool_results)
    return out


def _attach_proto_fields(out: dict, ir: IRRequest, system_key: str, max_tokens_key: str) -> dict:
    """Append protocol-mapped optional request fields shared by all three serializers."""
    if ir.system is not None:
        out[system_key] = ir.system
    if ir.tools is not None:
        out["tools"] = ir.tools
    if ir.max_tokens is not None:
        out[max_tokens_key] = ir.max_tokens
    if ir.temperature is not None:
        out["temperature"] = ir.temperature
    if ir.stream:
        out["stream"] = True
    return out


def serialize_anthropic(ir: IRRequest) -> dict:
    out = {"model": ir.model, "messages": _serialize_anthropic_messages(ir.messages)}
    return _attach_proto_fields(out, ir, "system", "max_tokens")


def serialize_responses(ir: IRRequest) -> dict:
    out = {"model": ir.model, "input": _serialize_responses_items(ir.messages)}
    return _attach_proto_fields(out, ir, "instructions", "max_output_tokens")


def serialize_chat(ir: IRRequest) -> dict:
    messages = _serialize_chat_messages(ir.messages)
    if ir.system is not None:
        messages.insert(0, {"role": "system", "content": ir.system})
    out = {"model": ir.model, "messages": messages}
    return _attach_proto_fields(out, ir, "system", "max_tokens")
