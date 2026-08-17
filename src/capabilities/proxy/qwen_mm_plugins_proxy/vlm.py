"""VLM backend client: OpenAI-compatible chat (primary) + Anthropic native; Ollama probe."""

from __future__ import annotations

import base64

import httpx

from .config import VLMConfig
from .ir import ImageBlock

TIER1_PROMPT = (
    "Describe the image in detail. Return structured evidence:\n"
    "- OCR: verbatim text visible in the image (line by line)\n"
    "- Layout: key regions in reading order\n"
    "- Key elements: objects, UI, people, numbers\n"
    "- uncertainty: anything you cannot determine\n"
    "Never invent content that is not visible."
)
TIER2_PROMPT = "Answer the question from the image. Include OCR evidence and explicit uncertainty.\nQuestion: {q}"


class VLMError(Exception):
    def __init__(self, reason: str, message: str = ""):
        super().__init__(message)
        self.reason = reason


class VLMClient:
    def __init__(self, cfg: VLMConfig):
        self.cfg = cfg
        self._http = httpx.Client(timeout=cfg.timeout_ms / 1000.0)

    # -- prompt ---------------------------------------------------------------
    def _prompt(self, question: str | None, tier: int) -> str:
        return TIER2_PROMPT.format(q=question) if tier == 2 and question else TIER1_PROMPT

    def _image_content(self, img: ImageBlock) -> dict:
        if img.url:
            return {"type": "image_url", "image_url": {"url": img.url}}
        b64 = img.base64 or base64.b64encode(b"").decode()
        return {"type": "image_url", "image_url": {"url": f"data:{img.media_type or 'image/png'};base64,{b64}"}}

    # -- calls ----------------------------------------------------------------
    def describe(self, image: ImageBlock, question: str | None = None, tier: int = 1) -> str:
        prompt = self._prompt(question, tier)
        try:
            if self.cfg.format == "anthropic":
                return self._describe_anthropic(image, prompt)
            return self._describe_chat(image, prompt)
        except VLMError:
            raise
        except httpx.TimeoutException as exc:
            raise VLMError("TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise VLMError("TRANSPORT", str(exc)) from exc

    def _describe_chat(self, image: ImageBlock, prompt: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, self._image_content(image)]}],
            "max_tokens": self.cfg.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        resp = self._http.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            raise VLMError(_classify(resp.status_code), resp.text[:200])
        return self._parse_response(resp, self._chat_text)

    def _describe_anthropic(self, image: ImageBlock, prompt: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/v1/messages"
        body = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image.media_type or "image/png",
                                "data": image.base64 or "",
                            },
                        },
                    ],
                }
            ],
        }
        headers = {"x-api-key": self.cfg.api_key, "anthropic-version": "2023-06-01"} if self.cfg.api_key else {}
        resp = self._http.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            raise VLMError(_classify(resp.status_code), resp.text[:200])
        return self._parse_response(resp, self._anthropic_text)

    @staticmethod
    def _parse_response(resp: httpx.Response, extract) -> str:
        """Parse JSON and map malformed shapes to a PARSE VLMError."""
        try:
            return extract(resp.json())
        except (KeyError, IndexError, ValueError, TypeError, AttributeError) as exc:
            raise VLMError("PARSE", str(exc)) from exc

    @staticmethod
    def _chat_text(data) -> str:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise VLMError("PARSE", f"chat content is not a string: {type(content).__name__}")
        return content

    @staticmethod
    def _anthropic_text(data) -> str:
        return "".join(block["text"] for block in data["content"] if block.get("type") == "text")


def _classify(status: int) -> str:
    if status == 401 or status == 403:
        return "AUTH"
    if status == 429:
        return "RATE_LIMIT"
    if status >= 500:
        return "HTTP"
    return "HTTP"


def probe_ollama(timeout_s: float = 2.0) -> str | None:
    """Return the first vision-capable Ollama model id, or None."""
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                return None
            for m in resp.json().get("models", []):
                name = m.get("name", "")
                if "vl" in name.lower() or "vision" in name.lower():
                    return name
    except httpx.HTTPError:
        return None
    return None
