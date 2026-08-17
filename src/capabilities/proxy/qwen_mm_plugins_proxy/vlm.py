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


# spec §5.4：可重试的 VLM 错误——网络(TRANSPORT)/超时(TIMEOUT)/限流(RATE_LIMIT)/上游 5xx(HTTP)；
# AUTH(401/403 认证错) / PARSE(响应格式错) 重试无意义，不重试。
VLM_RETRYABLE = {"TRANSPORT", "TIMEOUT", "RATE_LIMIT", "HTTP"}


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, VLMError) and exc.reason in VLM_RETRYABLE


class VLMClient:
    def __init__(self, cfg: VLMConfig):
        self.cfg = cfg
        self._http = httpx.Client(timeout=cfg.timeout_ms / 1000.0)
        # spec §6.3：无 key 且 auto_local_ollama 时，惰性探测本地 Ollama 作 VLM fallback。
        self._local_probed = False
        self._local_model: str | None = None

    def _resolve_local_model(self) -> str | None:
        """首次探测本地 Ollama 视觉模型（仅无 key 时）；未命中返回 None。"""
        if self.cfg.api_key or not self.cfg.auto_local_ollama:
            return None
        if not self._local_probed:
            self._local_probed = True
            self._local_model = probe_ollama()
        return self._local_model

    def _describe_local(self, image: ImageBlock, prompt: str) -> str:
        """用本地 Ollama（OpenAI 兼容 /v1）识图：图片不离开本机。"""
        url = "http://localhost:11434/v1/chat/completions"
        body = {
            "model": self._local_model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, self._image_content(image)]}],
            "max_tokens": self.cfg.max_tokens,
        }
        resp = self._http.post(url, json=body, headers={})
        if resp.status_code != 200:
            raise VLMError(_classify(resp.status_code), resp.text[:200])
        return self._parse_response(resp, self._chat_text)

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
        if self._resolve_local_model() is not None:
            try:
                return self._describe_local(image, prompt)
            except (VLMError, httpx.HTTPError) as exc:
                # 本地失败回退云端端点（fail-open）；本地探测结果已缓存，避免重复探测
                self._local_model = None
                raise VLMError("TRANSPORT", f"local ollama failed: {exc}") from exc
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
        url = self.cfg.base_url.rstrip("/") + "/messages"
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
    return "HTTP"


def probe_ollama(timeout_s: float = 2.0) -> str | None:
    """Return the first vision-capable Ollama model id, or None."""
    try:
        with httpx.Client(timeout=timeout_s, trust_env=False) as client:
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
