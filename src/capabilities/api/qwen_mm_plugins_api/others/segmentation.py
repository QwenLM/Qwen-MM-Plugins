"""MCP tool: image segmentation via SAM3 server."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Optional

from pydantic import BaseModel

from shared.env import get_env


class SegmentationArgs(BaseModel):
    image_path: str
    prompt: str
    server: Optional[str] = None


TOOL = {"name": "segmentation", "args": SegmentationArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Segment objects in an image using a SAM3 server. Provide a text prompt describing what to
    segment. Returns mask metadata and a visualization image with masks overlaid.

    Args:
        image_path: Absolute path to the image file
        prompt: What to segment (e.g., 'the cat', 'blue car', 'all people')
        server: SAM3 server URL (defaults to SAM3_SERVER_URL env var)
    """
    from shared.content import image, require_dep, require_file, text, text_error

    image_path = arguments.get("image_path", "")
    prompt = arguments.get("prompt", "")
    server = (arguments.get("server") or get_env("SAM3_SERVER_URL") or "").rstrip("/")

    if not server:
        return text_error("SAM3 server URL not set. Pass 'server' or set SAM3_SERVER_URL env var.")
    if err := require_file(image_path):
        return err
    if err := require_dep("requests"):
        return err

    import requests

    try:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()

        resp = requests.post(
            f"{server}/segment",
            json={"image_b64": image_b64, "prompt": prompt, "return_img": True},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.ConnectionError:
        return text_error(f"cannot connect to SAM3 server at {server}")
    except Exception as e:
        # timeouts, non-2xx (raise_for_status), non-JSON bodies, etc.
        return text_error(f"SAM3 request to {server} failed: {e}")

    result = {
        "image": os.path.basename(image_path),
        "prompt": prompt,
        "num_masks": data.get("num_masks", 0),
        "results": [
            {"score": round(r["score"], 4), "box": [round(x, 1) for x in r["box"]]} for r in data.get("results", [])
        ],
    }

    content: list[dict[str, Any]] = [text(json.dumps(result, indent=2, ensure_ascii=False))]

    vis_b64 = data.get("image_b64")
    if vis_b64 and data.get("num_masks", 0) > 0:
        content.append(image(vis_b64, "image/png"))

    return content
