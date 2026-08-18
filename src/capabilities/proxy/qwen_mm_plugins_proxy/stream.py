"""SSE streaming: same-protocol passthrough + Anthropic <-> Chat event translation (Phase 1)."""

from __future__ import annotations

import json
from collections.abc import Iterable


def _data_payload(line: str) -> dict | None:
    if not line.startswith("data: "):
        return None
    payload = line[len("data: ") :].strip()
    if payload in ("[DONE]", ""):
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def stream_same_protocol(inbound: Iterable[str]) -> Iterable[str]:
    yield from inbound


def translate_anthropic_to_chat(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = _data_payload(line)
        if payload is None:
            # [DONE] / empty / malformed: no chat equivalent in Phase 1 -> skip
            continue
        kind = payload.get("type")
        if kind == "content_block_delta" and payload.get("delta", {}).get("type") == "text_delta":
            yield "data: " + json.dumps(
                {"choices": [{"delta": {"content": payload["delta"]["text"]}}]},
                separators=(",", ":"),
            )
        # message_stop / others: no chat equivalent in Phase 1 -> drop silently
    yield "data: [DONE]"


def translate_chat_to_anthropic(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = _data_payload(line)
        if payload is None:
            continue
        choices = payload.get("choices") or []
        delta = choices[0].get("delta", {}) if choices else {}
        text = delta.get("content")
        if text:
            yield "data: " + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                },
                separators=(",", ":"),
            )
    yield 'data: {"type":"message_stop"}'
