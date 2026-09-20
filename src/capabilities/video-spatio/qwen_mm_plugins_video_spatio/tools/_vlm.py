"""Small image/question adapter over the shared OpenAI-compatible VL client."""

from __future__ import annotations

import base64
import io
from typing import Any

_LOCATE_SYS = (
    "You locate objects in images. First verify presence (PRESENT/ABSENT/AMBIGUOUS). "
    "For a point output '(x=<v>, y=<v>)', for a box '(x1=..,y1=..,x2=..,y2=..)', values 0-1000 "
    "normalized (x=horizontal 0=left..1000=right, y=vertical 0=top..1000=bottom), NOT pixels. "
    "Keep the x=/y= labels. If not visible, say 'Not visible' — do not guess a coordinate."
)
_THINK_SYS = (
    "Reason step by step, grounding every claim in what is visible. If it cannot be determined from the images, say so."
)
_NOTHINK_SYS = "Answer directly and concisely from the image. If it cannot be determined, say so."


def _to_jpeg_b64(image) -> str:
    img = getattr(image, "image", image)  # unwrap FrameImage
    if not hasattr(img, "save"):
        from PIL import Image

        img = Image.fromarray(img) if not isinstance(img, Image.Image) else img
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class VLMShim:
    """Minimal VLMModule replacement: images + a question → text, over one external endpoint."""

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        from shared.api_openai import resolve_openai_endpoint, resolve_vl_model

        self._base_url, self._api_key = resolve_openai_endpoint({"base_url": base_url, "api_key": api_key})
        self._model = resolve_vl_model(model)

    def _dispatch(self, images, question: str, system_prompt: str) -> str:
        from shared.api_openai import call_openai_chat

        imgs = images if isinstance(images, (list, tuple)) else [images]
        content: list[dict[str, Any]] = [{"type": "text", "text": question}]
        for im in imgs:
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _to_jpeg_b64(im)}})
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
        resp = call_openai_chat(base_url=self._base_url, api_key=self._api_key, model=self._model, messages=messages)
        try:
            return resp.choices[0].message.content or ""
        except Exception:  # noqa: BLE001
            return str(resp)

    def ask(self, images, question: str, *args, **kwargs) -> str:
        return self._dispatch(images, question, _NOTHINK_SYS)

    def ask_with_thinking(self, images, question: str, *args, **kwargs) -> str:
        return self._dispatch(images, question, _THINK_SYS)

    def locate(self, images, question: str, *args, **kwargs) -> str:
        return self._dispatch(images, question, _LOCATE_SYS)
