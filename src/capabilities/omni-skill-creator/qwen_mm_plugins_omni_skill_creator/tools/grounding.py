"""MCP tool: locate a described region in an image via a VLM (grounding).

The single-target prompt and pixel-box contract belong to this capability. Connection settings,
provider credentials, retries and image orientation use the shared VL helpers shipped with every
server; no sibling capability package is imported.

Coordinates come back as PIXELS of the display-oriented image, ready to hand to `image_annotate` with
`coord_space="pixel"` — the model is asked for the 0-1000 normalized convention and the
conversion happens here, because a box is only useful if it lands on the target.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from shared.api_openai import (
    call_openai_chat,
    encode_image_source,
    resolve_openai_endpoint,
    resolve_vl_model,
)
from shared.image import open_image

from ._media_utils import MediaOpsError

if TYPE_CHECKING:
    from PIL import Image


SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

PROMPT = (
    "Locate {target} in this image.\n"
    "Answer with ONLY a JSON array, one object per instance:\n"
    '[{{"label": "<short name>", "bbox": [x1, y1, x2, y2]}}]\n'
    "bbox is the top-left and bottom-right corner in coordinates NORMALIZED to 0-1000 on each "
    "axis independently (x/1000 of the width, y/1000 of the height), NOT pixels. Make each box "
    "tight around the thing itself, not the region around it. If it does not appear in the "
    "image, answer []."
)


class GroundingArgs(BaseModel):
    image_path: str = Field()
    target: str = Field()
    model: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(default=None)


TOOL: dict[str, Any] = {"name": "grounding", "args": GroundingArgs}


def _validate_image(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise MediaOpsError("`image_path` must be a non-empty local image file path.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise MediaOpsError(f"Image file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise MediaOpsError(
            f"Unsupported image extension `{path.suffix}`. Supported: {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}"
        )
    return path


def parse_boxes(text: str, width: int, height: int) -> tuple[list[dict[str, Any]], str, str]:
    """Parse the model's JSON into pixel boxes; return (detections, space, warning).

    The prompt asks for 0-1000. A coordinate above that can be pixels of the source image OR
    pixels of whatever size the model internally resized to — the two are indistinguishable
    from here, and `qwen-vl-plus` was observed answering in the latter. So such boxes are
    still returned (they may be right) but carry a warning telling the caller to check the
    close-up before trusting them.
    """
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    raw = (match.group(1) if match else text).strip()
    if not raw.startswith(("[", "{")):
        bracket = re.search(r"\[.*\]", raw, re.DOTALL)
        raw = bracket.group(0) if bracket else raw
    # Observed malformation: a stray quote glued to a number (`[1039, 124, 1115, 186"]`).
    raw = re.sub(r'(?<=\d)"(?=\s*[,\]\}])', "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], "unparsed", ""
    if isinstance(data, dict):
        data = data.get("detections") or data.get("objects") or data.get("results") or []
    if not isinstance(data, list):
        return [], "unparsed", ""

    raw_boxes: list[tuple[str, list[float]]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        box = item.get("bbox") or item.get("box") or item.get("bbox_2d")
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            coords = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        label = str(item.get("label") or item.get("name") or item.get("object") or "")
        raw_boxes.append((label, coords))

    if not raw_boxes:
        return [], "none", ""

    space = "pixel" if any(v > 1000 for _, c in raw_boxes for v in c) else "norm"
    warning = ""
    if space == "pixel":
        warning = (
            "The model answered outside the 0-1000 range, so these are read as pixels of this "
            f"{width}x{height} image. If it actually measured a resized copy, the box is wrong — "
            "check the close-up from image_annotate before shipping the asset, or retry with a "
            "model that honours the normalized convention."
        )
    detections = []
    for label, coords in raw_boxes:
        if space == "norm":
            px = [
                coords[0] / 1000 * width,
                coords[1] / 1000 * height,
                coords[2] / 1000 * width,
                coords[3] / 1000 * height,
            ]
        else:
            px = list(coords)
        x1, x2 = sorted((px[0], px[2]))
        y1, y2 = sorted((px[1], px[3]))
        detections.append(
            {
                "label": label,
                "bbox_px": [
                    max(0, round(x1)),
                    max(0, round(y1)),
                    min(width, round(x2)),
                    min(height, round(y2)),
                ],
                "bbox_norm": [round(c) for c in coords] if space == "norm" else None,
            }
        )
    return detections, space, warning


def _call(image: Image.Image, target: str, model: str, *, base_url: str, api_key: str) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                encode_image_source(image),
                {"type": "text", "text": PROMPT.format(target=target)},
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
            optional_extra_body={"enable_thinking": False},
        )
    except Exception as exc:
        raise MediaOpsError(f"grounding request failed: {type(exc).__name__}: {exc}") from exc
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError) as exc:
        raise MediaOpsError("unexpected grounding response: missing message content") from exc


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Locate something described in words inside an image and get its PIXEL box back — the measured
    coordinates to pass to `image_annotate` with `coord_space="pixel"`. Use it for targets that carry no
    text: an icon, a handle, a shape, a region of a chart. When the target DOES have a visible label (a
    button, menu item, field), prefer `ocr_frames` with `locate`: it is offline, exact, and cheaper.
    Uses the shared VL endpoint and provider credential. Pixel coordinates follow the image's EXIF
    display orientation, as image_annotate does. If the call fails, the tool says so rather than
    returning a guessed box.

    Args:
        image_path: Absolute path to the image (e.g. a frame from crop_frame).
        target: What to locate, described the way you'd point it out to someone: 'the Zoom button in the
            ribbon', 'the red slider handle', 'the hexagon logo'. One target per call reads more reliably than a
            list.
        model: Override the VLM (default: QWEN_MM_API_VL_MODEL, else qwen3.7-plus).
        base_url: Optional API root override; otherwise DASHSCOPE_BASE_URL and the shared default.
        api_key: Optional credential override; otherwise selected by the endpoint's provider.
    """
    path = _validate_image(arguments["image_path"])
    target = (arguments.get("target") or "").strip()
    if not target:
        raise MediaOpsError("`target` must describe what to locate, e.g. 'the Zoom button'.")
    model = resolve_vl_model(arguments.get("model"))
    base_url, api_key = resolve_openai_endpoint(arguments)

    with open_image(str(path)) as img:
        width, height = img.size
        text = _call(img, target, model, base_url=base_url, api_key=api_key)

    detections, space, warning = parse_boxes(text, width, height)

    result = {
        "image": path.name,
        "size": {"w": width, "h": height},
        "target": target,
        "model": model,
        "coordinates_returned_by_model": space,
        "detections": detections,
        "hint": ('bbox_px is in pixels of this image — pass it to image_annotate with coord_space="pixel"'),
    }
    if warning:
        result["coordinate_warning"] = warning
    if not detections:
        result["raw_response"] = text[:600]
        result["hint"] = (
            "No box parsed. Either the target isn't in the frame, or the answer wasn't JSON — "
            "see raw_response before trying a differently worded target."
        )
    return [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
