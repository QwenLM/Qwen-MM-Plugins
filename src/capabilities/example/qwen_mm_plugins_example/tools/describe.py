"""Example tool (API call): ask an OpenAI-compatible chat model, optionally about an image.

Demonstrates the API-tool pattern (mirrors core's apis/ocr.py):
  1. resolve the endpoint + key via shared.env (through resolve_openai_endpoint — get_env under the hood),
  2. optionally attach a local image with require_file + encode_image_source,
  3. call the shared chat client and return the reply as text.

`dry_run=true` returns the request that WOULD be sent — no API key, no network — so the tool is
inspectable and testable offline.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.api_openai import DEFAULT_MODEL, call_openai_chat, encode_image_source, resolve_openai_endpoint
from shared.content import require_dep, require_file, text, text_error


class DescribeArgs(BaseModel):
    prompt: str
    image_path: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    dry_run: bool = False


TOOL = {"name": "describe", "args": DescribeArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask an OpenAI-compatible chat model a question, optionally about a local image. Demonstrates an
    API-calling tool whose endpoint/key are resolved via shared.env. Set dry_run=true to see the
    request without a key or network call.

    Args:
        prompt: What to ask the model (e.g. 'Describe this image', or any question).
        image_path: Optional local image file to attach to the prompt.
        model: Model id override (else the shared default).
        api_key: Override DASHSCOPE_API_KEY (else environment / config).
        base_url: Override the OpenAI-compatible base URL.
        dry_run: Return the request that WOULD be sent, without calling the API.
    """
    prompt = arguments.get("prompt", "")
    image_path = arguments.get("image_path")
    model = arguments.get("model") or DEFAULT_MODEL

    # Endpoint + key: explicit args > DASHSCOPE_BASE_URL/DASHSCOPE_API_KEY (env or config) > defaults.
    base_url, api_key = resolve_openai_endpoint(arguments)

    # Build the OpenAI-style user message: optional image part, then the text prompt.
    parts: list[dict[str, Any]] = []
    if image_path:
        if err := require_file(image_path):
            return err
        parts.append(encode_image_source(image_path))
    parts.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": parts}]

    if arguments.get("dry_run"):
        preview = {"base_url": base_url, "model": model, "messages": messages, "has_api_key": bool(api_key)}
        return [text("DRY RUN — request that would be sent:\n" + json.dumps(preview, ensure_ascii=False, indent=2))]

    if err := require_dep("openai"):
        return err
    try:
        resp = call_openai_chat(base_url=base_url, api_key=api_key, model=model, messages=messages, max_tokens=1024)
        return [text(resp.choices[0].message.content or "")]
    except Exception as e:  # noqa: BLE001 — surface any API/SDK error as a tool message, not a crash
        return text_error(str(e))
