"""MCP tool: object detection / grounding via vision-language model."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from shared.image import draw_boxes, norm_to_pixel


class GroundingArgs(BaseModel):
    image_path: str = Field(description="Absolute path to the image file")
    prompt: str = Field(description="What to detect (e.g., 'all cats', 'the red car', 'every person')")
    model: Optional[str] = Field(
        default=None,
        description="Model name (default: 'qwen3.7-plus'). Always uses the default model.",
    )
    return_img: bool = Field(default=False, description="Return annotated image with bounding boxes drawn")
    api_key: Optional[str] = Field(default=None, description="API key (defaults to DASHSCOPE_API_KEY)")
    base_url: Optional[str] = Field(default=None, description="API base URL (defaults to DASHSCOPE_BASE_URL)")


TOOL: dict[str, Any] = {
    "name": "grounding",
    "description": (
        "Detect and locate objects in an image using a vision-language model. "
        "Returns bounding box coordinates mapped to original image pixel dimensions. "
        "Optionally draws boxes on the image and returns the annotated image."
    ),
    "args": GroundingArgs,
}


def _parse_json(text: str, img_w: int, img_h: int) -> list[dict[str, Any]] | None:
    """Try JSON format: [{"label": "...", "bbox_2d": [x1,y1,x2,y2]}]."""
    # Strip markdown code fences if present
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    raw = m.group(1).strip() if m else text.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if isinstance(data, dict):
        data = data.get("detections") or data.get("objects") or data.get("results") or []
    if not isinstance(data, list):
        return None

    results: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or item.get("name") or item.get("object") or ""
        bbox = item.get("bbox_2d") or item.get("bbox") or item.get("box") or item.get("bounding_box")
        if not bbox or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        bbox_norm = [int(v) for v in bbox]
        results.append(
            {
                "label": str(label),
                "bbox_pixel": norm_to_pixel(bbox_norm, img_w, img_h),
                "bbox_normalized": bbox_norm,
            }
        )

    return results if results else None


def _parse_ref_box(text: str, img_w: int, img_h: int) -> list[dict[str, Any]]:
    """Fallback: parse <ref>label</ref><box>(x1,y1),(x2,y2)</box> format."""
    results: list[dict[str, Any]] = []
    ref_re = re.compile(r"<ref>(.*?)</ref>")
    box_re = re.compile(r"<box>\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*,\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)</box>")

    refs = list(ref_re.finditer(text))
    for i, ref_match in enumerate(refs):
        label = ref_match.group(1)
        start = ref_match.end()
        end = refs[i + 1].start() if i + 1 < len(refs) else len(text)
        region = text[start:end]

        for box_match in box_re.finditer(region):
            bbox_norm = [int(box_match.group(j)) for j in range(1, 5)]
            results.append(
                {
                    "label": label,
                    "bbox_pixel": norm_to_pixel(bbox_norm, img_w, img_h),
                    "bbox_normalized": bbox_norm,
                }
            )

    return results


def parse_grounding(text: str, img_w: int, img_h: int) -> list[dict[str, Any]]:
    """Parse model output — try JSON first, fall back to ref/box tags."""
    return _parse_json(text, img_w, img_h) or _parse_ref_box(text, img_w, img_h)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from shared.api_openai import call_openai_chat, resolve_vl_model, resolve_openai_endpoint
    from shared.content import require_dep, require_file

    image_path = arguments.get("image_path", "")
    prompt = arguments.get("prompt", "")
    model = arguments.get("model") or resolve_vl_model()
    should_draw = arguments.get("return_img", False)
    base_url, api_key = resolve_openai_endpoint(arguments)

    if err := require_file(image_path):
        return err
    if err := require_dep("PIL", "pillow"):
        return err
    if err := require_dep("openai"):
        return err

    from PIL import Image

    from shared.api_openai import encode_image_source

    img = Image.open(image_path)
    orig_w, orig_h = img.size

    grounding_prompt = (
        f"请检测并定位图片中的{prompt}。"
        "以JSON数组格式输出，每个对象一个元素：\n"
        '[{"label": "名称", "bbox_2d": [x1, y1, x2, y2]}]\n'
        "bbox_2d为归一化坐标(0-1000)，表示左上角和右下角。仅输出JSON。"
    )

    messages = [
        {
            "role": "user",
            "content": [
                encode_image_source(image_path),
                {"type": "text", "text": grounding_prompt},
            ],
        }
    ]

    try:
        response = call_openai_chat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            max_tokens=2048,
            # Grounding is a perception task, no need to think
            extra_body={"enable_thinking": False},
        )
        raw_text = response.choices[0].message.content or ""
        detections = parse_grounding(raw_text, orig_w, orig_h)
    except Exception as e:
        from shared.content import text_error

        return text_error(f"{e}")

    result = {
        "image": os.path.basename(image_path),
        "image_size": {"width": orig_w, "height": orig_h},
        "detections": detections,
        "raw_response": raw_text,
    }

    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)},
    ]

    if should_draw and detections:
        from shared.image import image_to_content

        content.append(image_to_content(draw_boxes(img, detections), "large"))

    return content
