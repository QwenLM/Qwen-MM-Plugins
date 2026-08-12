"""MCP tool: OCR text extraction via vision-language model."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class OcrArgs(BaseModel):
    image_path: str = Field(description="Absolute path to the image file")
    prompt: Optional[str] = Field(
        default=None,
        description=(
            "Custom OCR instruction. Default extracts all visible text. "
            "Override to focus on specific regions or languages."
        ),
    )
    model: Optional[str] = Field(
        default=None,
        description="Model id override. Defaults to QWEN_MM_API_VL_MODEL, then 'qwen3.7-plus'.",
    )
    api_key: Optional[str] = Field(default=None, description="API key (defaults to DASHSCOPE_API_KEY)")
    base_url: Optional[str] = Field(default=None, description="API base URL (defaults to DASHSCOPE_BASE_URL)")


TOOL: dict[str, Any] = {
    "name": "ocr",
    "description": (
        "Extract text from an image using a vision-language model. "
        "Supports printed text, handwriting, documents, signs, and more. "
        "Returns the recognized text content."
    ),
    "args": OcrArgs,
}

DEFAULT_PROMPT = "请对这张图片进行OCR文字识别，提取图片中所有可见的文字内容，保持原始排版格式。"


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from shared.api_openai import call_openai_chat, resolve_openai_endpoint, resolve_vl_model
    from shared.content import require_dep, require_file

    image_path = arguments.get("image_path", "")
    prompt = arguments.get("prompt") or DEFAULT_PROMPT
    model = resolve_vl_model(arguments.get("model"))
    base_url, api_key = resolve_openai_endpoint(arguments)

    if err := require_file(image_path):
        return err
    if err := require_dep("openai"):
        return err

    from shared.api_openai import encode_image_source

    messages = [
        {
            "role": "user",
            "content": [
                encode_image_source(image_path),
                {"type": "text", "text": prompt},
            ],
        }
    ]

    try:
        response = call_openai_chat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            max_tokens=4096,
        )
    except Exception as e:
        from shared.content import text_error

        return text_error(f"{e}")
    return [{"type": "text", "text": response.choices[0].message.content or ""}]
