"""MCP tool: image generation, editing, and translation via Qwen-Image."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from shared.content import text_error
from shared.env import get_env


class QwenImageArgs(BaseModel):
    mode: Literal["text_to_image", "image_edit", "image_translate"]
    prompt: Optional[str] = None
    image_urls: Optional[list[str]] = None
    size: str = "1024*1024"
    negative_prompt: str = "低分辨率，低画质，肢体畸形，画面过饱和"
    prompt_extend: bool = True
    seed: Optional[int] = None
    n: int = 1
    source_lang: str = "zh"
    target_lang: str = "en"
    output_dir: Optional[str] = None


TOOL = {"name": "qwen_image", "args": QwenImageArgs}


def _text_to_image(arguments: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    import dashscope
    from dashscope import MultiModalConversation

    from shared.api_dashscope import API_V1, retry_call

    dashscope.base_http_api_url = API_V1

    prompt = arguments.get("prompt", "")
    if not prompt:
        return text_error("prompt is required for text_to_image mode")

    messages = [{"role": "user", "content": [{"text": prompt}]}]

    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": "qwen-image-2.0-pro",
        "messages": messages,
        "result_format": "message",
        "stream": False,
        "watermark": False,
        "prompt_extend": arguments.get("prompt_extend", True),
        "negative_prompt": arguments.get("negative_prompt", "低分辨率，低画质，肢体畸形，画面过饱和"),
        "size": arguments.get("size", "1024*1024"),
    }
    if arguments.get("seed") is not None:
        kwargs["seed"] = arguments["seed"]
    if arguments.get("n", 1) > 1:
        kwargs["n"] = arguments["n"]

    response = retry_call(MultiModalConversation.call, **kwargs)

    if response.status_code != 200:
        return text_error(f"[{response.code}] {response.message}")

    image_urls = []
    for content in response.output.choices[0].message.content:
        if "image" in content:
            image_urls.append(content["image"])

    return _format_image_result(image_urls, arguments, "text_to_image")


def _image_edit(arguments: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    import dashscope
    from dashscope import MultiModalConversation

    from shared.api_dashscope import API_V1, retry_call

    dashscope.base_http_api_url = API_V1

    prompt = arguments.get("prompt", "")
    image_urls = arguments.get("image_urls", [])
    if not prompt:
        return text_error("prompt is required for image_edit mode")
    if not image_urls:
        return text_error("image_urls is required for image_edit mode (1-3 images)")

    content: list[dict[str, str]] = []
    for url in image_urls[:3]:
        content.append({"image": url})
    content.append({"text": prompt})
    messages = [{"role": "user", "content": content}]

    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": "qwen-image-2.0-pro",
        "messages": messages,
        "stream": False,
        "watermark": False,
        "prompt_extend": arguments.get("prompt_extend", True),
        "negative_prompt": arguments.get("negative_prompt", " "),
        "size": arguments.get("size", "1024*1024"),
    }
    if arguments.get("seed") is not None:
        kwargs["seed"] = arguments["seed"]
    if arguments.get("n", 1) > 1:
        kwargs["n"] = arguments["n"]

    response = retry_call(MultiModalConversation.call, **kwargs)

    if response.status_code != 200:
        return text_error(f"[{response.code}] {response.message}")

    result_urls = []
    for content_item in response.output.choices[0].message.content:
        if "image" in content_item:
            result_urls.append(content_item["image"])

    return _format_image_result(result_urls, arguments, "image_edit")


def _image_translate(arguments: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    from shared.api_dashscope import API_V1, poll_dashscope_task, submit_dashscope_async

    image_urls = arguments.get("image_urls", [])
    if not image_urls:
        return text_error("image_urls is required for image_translate mode (1 image)")

    source_lang = arguments.get("source_lang", "zh")
    target_lang = arguments.get("target_lang", "en")

    task_id, result = submit_dashscope_async(
        "services/aigc/image2image/image-synthesis",
        {
            "model": "qwen-mt-image",
            "input": {
                "image_url": image_urls[0],
                "source_lang": source_lang,
                "target_lang": target_lang,
                "ext": {"config": {"imageSegment": False}},
            },
        },
        api_key,
    )
    if not task_id:
        return text_error(f"failed to create task: {json.dumps(result, ensure_ascii=False)}")
    # Guard the poll URL: a malformed task_id in the response must not be interpolated into it.
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 128:
        return text_error(f"invalid task_id in response: {json.dumps(result, ensure_ascii=False)}")
    task_id = task_id.strip()

    poll_resp = poll_dashscope_task(task_id, api_key, base_url=API_V1, interval=3, timeout=120)
    status = poll_resp.get("output", {}).get("task_status")
    if status == "SUCCEEDED":
        image_url = poll_resp["output"].get("image_url")
        if image_url:
            return _format_image_result([image_url], arguments, "image_translate")
        return text_error(f"no image_url in response: {json.dumps(poll_resp, ensure_ascii=False)}")
    if status == "FAILED":
        return text_error(f"translation failed: {json.dumps(poll_resp, ensure_ascii=False)}")
    return text_error(f"translation timed out after 120s (task_id: {task_id})")


def _format_image_result(
    image_urls: list[str],
    arguments: dict[str, Any],
    mode: str,
) -> list[dict[str, Any]]:

    lines = [f"**Mode**: {mode}", f"**Generated {len(image_urls)} image(s)**:"]
    for i, url in enumerate(image_urls, 1):
        lines.append(f"  {i}. {url}")

    output_dir = arguments.get("output_dir")
    if output_dir and image_urls:
        from shared.api_dashscope import save_url_to_dir

        out_path = Path(output_dir).expanduser().resolve()
        saved = []
        for i, url in enumerate(image_urls):
            fname = hashlib.sha256(url.encode()).hexdigest()[:16]
            file_path = out_path / f"img_{mode}_{fname}_{i}.png"
            save_url_to_dir(url, str(file_path), timeout=60)
            saved.append(str(file_path))
        lines.append("")
        lines.append("**Saved to**:")
        for p in saved:
            lines.append(f"  - {p}")

    return [{"type": "text", "text": "\n".join(lines)}]


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Image generation, editing, and translation using Qwen-Image models. Three modes: (1)
    text_to_image — generate image from text prompt (sync, qwen-image-2.0-pro); (2) image_edit —
    edit an existing image with text instruction, supports up to 3 input images (sync, qwen-
    image-2.0-pro); (3) image_translate — translate text in an image while preserving layout (async,
    qwen-mt-image). All generated image URLs expire in 24 hours — download promptly.

    Args:
        mode: Operation mode. text_to_image: generate from text prompt only. image_edit: modify
            existing image(s) with text instruction. image_translate: translate text in image
            preserving layout.
        prompt: For text_to_image: image description prompt. For image_edit: editing instruction
            (e.g. 'convert to watercolor style'). Not used for image_translate.
        image_urls: Input image URL(s). For image_edit: 1-3 image URLs to edit/combine. For
            image_translate: exactly 1 image URL. Not used for text_to_image.
        size: Output image size (width*height). Common options: '1024*1024' (square), '2048*2048'
            (hi-res square), '2688*1536' (16:9 landscape), '1536*2688' (9:16 portrait), '2368*1728'
            (4:3). Total pixels: 512*512 to 2048*2048. Default: '1024*1024'.
        negative_prompt: Things to avoid in the generated image. Default: '低分辨率，低画质，肢体畸形，画面过饱和'.
            Only for text_to_image and image_edit modes.
        prompt_extend: Enable intelligent prompt rewriting for better results. Set to false when you
            need precise control. Default: true.
        seed: Random seed for reproducibility. Fix seed while iterating prompts to compare changes.
            Omit for random.
        n: Number of images to generate (1-4). Default: 1.
        source_lang: Source language for image_translate mode. Options: zh, en, ja, ko, fr, de, es,
            ru, etc. Default: zh.
        target_lang: Target language for image_translate mode. Options: zh, en, ja, ko, fr, de, es,
            ru, etc. Default: en.
        output_dir: Directory to save generated images. If provided, downloads images to this
            directory. If omitted, returns URLs only (valid 24h).
    """
    mode = arguments.get("mode", "text_to_image")

    api_key = get_env("DASHSCOPE_API_KEY")
    if not api_key:
        return text_error("DASHSCOPE_API_KEY not set")

    try:
        if mode == "text_to_image":
            return _text_to_image(arguments, api_key)
        elif mode == "image_edit":
            return _image_edit(arguments, api_key)
        elif mode == "image_translate":
            return _image_translate(arguments, api_key)
        else:
            return text_error(f"unknown mode '{mode}'. Use: text_to_image, image_edit, image_translate")
    except Exception as e:
        return text_error(f"{e}")
