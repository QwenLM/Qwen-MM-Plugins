"""LLM client for the graph-memory build pipeline (DashScope VLM + JSON extraction)."""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

import requests
from env_config import get_env, get_int_env

MAX_RETRIES = 60
RETRYABLE_STATUS_CODES = {418, 429, 500, 502, 503}


def extract_json(text: str) -> Any:
    """Parse JSON from a model response, tolerating ```json fences and surrounding prose."""
    text = text.strip()
    think_end = text.find("</think>")
    if think_end != -1:
        text = text[think_end + len("</think>") :].strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    if not text.startswith(("{", "[")):
        for i, ch in enumerate(text):
            if ch in ("{", "["):
                text = text[i:]
                break
    bracket = "]" if text.startswith("[") else "}"
    idx = text.rfind(bracket)
    if idx != -1:
        text = text[: idx + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"[error] JSON parse failed: {text[:500]}")
        raise


def call_vlm_curl(
    prompt: str,
    video_url: str | None = None,
    frame_items: list[tuple[str, str]] | None = None,
    model: str | None = None,
    api_key: str = "",
    temperature: float = 0.7,
    max_output_tokens: int = 65536,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Call DashScope VLM with a video URL, base64 frames, or plain text."""
    model = model or get_env("QWEN_MM_API_VL_MODEL")
    api_url = get_env("DASHSCOPE_BASE_URL").rstrip("/") + "/chat/completions"
    timeout = get_int_env("QWEN_MM_CHAT_TIMEOUT")
    content: list[dict] = []
    if frame_items:
        ts_lines = [f"Frame {i + 1}: {label}" for i, (_, label) in enumerate(frame_items)]
        ts_block = "Frame timestamps (corresponding to frames in order):\n" + "\n".join(ts_lines)
        for url, _ in frame_items:
            content.append({"type": "image_url", "image_url": {"url": url}})
        prompt = f"{ts_block}\n\n{prompt}"
    elif video_url:
        content.append({"type": "video_url", "video_url": {"url": video_url}})
    content.append({"type": "text", "text": prompt})
    if len(content) == 1 and content[0]["type"] == "text":
        msg_content = content[0]["text"]
    else:
        msg_content = content
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": msg_content}],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }

    if not api_key:
        api_key = get_env("DASHSCOPE_API_KEY") or ""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if not api_key.startswith("Bearer ") else api_key,
    }

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                api_url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False),
                timeout=timeout,
            )

            data = resp.json()

            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
                error_code = data["error"].get("code", "")
                if resp.status_code == 400:
                    raise RuntimeError(f"400 Bad Request: {error_msg}")
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    raise RuntimeError(f"Retryable HTTP {resp.status_code}: {error_msg}")
                raise RuntimeError(f"API error {error_code} (HTTP {resp.status_code}): {error_msg}")

            if resp.status_code != 200 and resp.status_code in RETRYABLE_STATUS_CODES:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

            if "choices" in data:
                return data["choices"][0]["message"]["content"]

            raise RuntimeError(f"No choices: {json.dumps(data)[:500]}")
        except Exception as exc:
            last_exc = exc
            err_msg = str(exc)
            if "400 Bad Request" in err_msg:
                raise
            if attempt == max_retries:
                raise
            delay = min(2**attempt + random.uniform(0, 1), 60.0)
            host = api_url.split("//")[1].split("/")[0] if "//" in api_url else api_url
            print(f"[retry] attempt {attempt + 1}/{max_retries} ({host}): {err_msg[:250]}, waiting {delay:.1f}s")
            time.sleep(delay)

    raise last_exc or RuntimeError("All retries exhausted")
