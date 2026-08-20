"""Convert MCP image results to captions when the host model is text-only."""

from __future__ import annotations

import json
import logging
from typing import Any

from shared.env import get_bool_env

log = logging.getLogger(__name__)

_BATCH_SIZE = 8
_MAX_TOKENS = 32 * 1024
_MISSING_KEY = "QWEN_MM_NATIVE_MODE=0 requires a non-empty DASHSCOPE_API_KEY."
_CAPTION_FAILED = "caption generation failed; check DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, and QWEN_MM_API_VL_MODEL."
_PROMPT = """Describe each input image for a text-only language model.

For every image, preserve the information needed to reason about it: faithfully transcribe visible
text, tables, labels, and values; describe charts, diagrams, UI state, objects, and spatial
relationships; distinguish observed details from uncertainty. Do not invent unseen details.

Return JSON only, exactly in this shape:
{"captions":["caption for image 1","caption for image 2"]}

The captions array must contain exactly one non-empty string per image, in input order.
"""


def adapt_content_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass images through in native mode, or replace them with generated captions."""
    if get_bool_env("QWEN_MM_NATIVE_MODE", default=True):
        return blocks

    positions = [
        index
        for index, block in enumerate(blocks)
        if isinstance(block, dict) and block.get("type") == "image" and isinstance(block.get("data"), str)
    ]
    if not positions:
        return blocks

    from shared.api_openai import resolve_openai_endpoint, resolve_vl_model

    base_url, api_key = resolve_openai_endpoint({})
    api_key = api_key.strip()
    if api_key in ("", "EMPTY"):
        return _replace_images(blocks, positions, [None] * len(positions), _MISSING_KEY)

    images = [blocks[index] for index in positions]
    model = resolve_vl_model()
    captions: list[str | None] = []
    for start in range(0, len(images), _BATCH_SIZE):
        batch = images[start : start + _BATCH_SIZE]
        try:
            captions.extend(
                _caption_batch(
                    batch,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                )
            )
        except Exception as exc:  # noqa: BLE001 — tool text remains useful if captioning fails
            status = getattr(exc, "status_code", None)
            suffix = f" HTTP {status}" if isinstance(status, int) else ""
            # Provider errors may echo request bodies, so never log the exception message.
            log.warning("visual captioning failed: %s%s", type(exc).__name__, suffix)
            captions.extend([None] * (len(images) - start))
            break

    return _replace_images(blocks, positions, captions, _CAPTION_FAILED)


def _replace_images(
    blocks: list[dict[str, Any]],
    positions: list[int],
    captions: list[str | None],
    failure: str,
) -> list[dict[str, Any]]:
    adapted = list(blocks)
    for position, caption in zip(positions, captions):
        text = f"[Generated visual caption]\n{caption}" if caption else f"[Visual content unavailable: {failure}]"
        adapted[position] = {"type": "text", "text": text}
    return adapted


def _caption_batch(
    images: list[dict[str, Any]],
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> list[str]:
    from shared.api_openai import call_openai_chat

    content: list[dict[str, Any]] = []
    for image in images:
        mime = image.get("mimeType", "image/jpeg")
        if not isinstance(mime, str) or not mime.startswith("image/"):
            mime = "image/jpeg"
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image['data']}"},
            }
        )
    content.append({"type": "text", "text": _PROMPT})

    response = call_openai_chat(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=_MAX_TOKENS,
        temperature=0,
        extra_body={"enable_thinking": False},
        optional_extra_body={"response_format": {"type": "json_object"}},
    )
    payload = json.loads(response.choices[0].message.content)
    captions = payload.get("captions") if isinstance(payload, dict) else None
    if not isinstance(captions, list) or len(captions) != len(images):
        raise ValueError("caption endpoint returned the wrong number of captions")
    captions = [caption.strip() for caption in captions if isinstance(caption, str)]
    if len(captions) != len(images) or any(not caption for caption in captions):
        raise ValueError("caption endpoint returned an invalid caption")
    return captions
