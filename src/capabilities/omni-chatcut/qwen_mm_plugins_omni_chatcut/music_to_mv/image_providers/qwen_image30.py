"""Alibaba Cloud Model Studio Qwen Image 3.0 synchronous adapter."""

from __future__ import annotations

import re

from .base import ImageProvider


class QwenImage30Provider(ImageProvider):
    name = "qwen_image_3"
    schema_version = "qwen-image-3.0/v1"
    output_suffix = ".png"
    result_url_ttl_sec = 24 * 60 * 60

    def __init__(self, config):
        super().__init__(config)
        workspace_id = self.config.get("workspace_id", "")
        if workspace_id:
            self.base_url = self.base_url.replace("{workspace_id}", str(workspace_id))

    def validate_configuration(self):
        super().validate_configuration()
        if "{" in self.base_url or "}" in self.base_url or "YOUR_WORKSPACE_ID" in self.base_url:
            raise ValueError(
                "image_providers.qwen_image_3.base_url contains an unresolved workspace placeholder; "
                "configure workspace_id/workspace_id_env or use https://dashscope.aliyuncs.com"
            )

    def normalized_size(self, image_config):
        raw = str(self.resolve_size(image_config) or "").lower().replace("x", "*")
        match = re.fullmatch(r"(\d+)\*(\d+)", raw)
        if not match:
            raise ValueError("Qwen Image 3.0 size must use WIDTH*HEIGHT, for example 2048*1152")
        width, height = (int(value) for value in match.groups())
        if width <= 0 or height <= 0:
            raise ValueError("Qwen Image 3.0 image dimensions must be positive")
        pixels = width * height
        if not 512 * 512 <= pixels <= 2048 * 2048:
            raise ValueError("Qwen Image 3.0 total pixels must be between 512*512 and 2048*2048")
        if max(width, height) / min(width, height) > 8:
            raise ValueError("Qwen Image 3.0 aspect ratio must be between 1:8 and 8:1")
        return f"{width}*{height}"

    def signature_parameters(self, image_config):
        values = super().signature_parameters(image_config)
        values.update(
            {
                "size": self.normalized_size(image_config),
                "n": int(self.config.get("n", 1)),
                "prompt_extend": bool(self.config.get("prompt_extend", False)),
                "prompt_extend_mode": self.config.get("prompt_extend_mode", "direct"),
                "enable_thinking": bool(self.config.get("enable_thinking", False)),
                "negative_prompt": image_config.get("negative_prompt_override", self.config.get("negative_prompt")),
                "seed": self.config.get("seed"),
                "watermark": bool(self.config.get("watermark", False)),
            }
        )
        return values

    def validate_request(self, image_urls, image_config):
        self.validate_configuration()
        self.normalized_size(image_config)
        if len(image_urls) > 3:
            raise ValueError(f"Qwen Image 3.0 accepts at most 3 reference images; got {len(image_urls)}")
        count = int(self.config.get("n", 1))
        if count != 1:
            raise ValueError("Music2MV requires image_providers.qwen_image_3.n=1")
        mode = self.config.get("prompt_extend_mode", "direct")
        if mode not in {"direct", "agent"}:
            raise ValueError("Qwen Image 3.0 prompt_extend_mode must be direct or agent")
        if image_urls and mode == "agent":
            raise ValueError("Qwen Image 3.0 agent prompt extension supports text-to-image only")
        seed = self.config.get("seed")
        if seed is not None and not 0 <= int(seed) <= 2147483647:
            raise ValueError("Qwen Image 3.0 seed must be between 0 and 2147483647")

    def build_payload(self, prompt, image_urls, image_config):
        image_urls = list(image_urls or [])
        self.validate_request(image_urls, image_config)
        content = [{"image": url} for url in image_urls]
        content.append({"text": prompt})
        parameters = {
            "prompt_extend": bool(self.config.get("prompt_extend", False)),
            "prompt_extend_mode": self.config.get("prompt_extend_mode", "direct"),
            "enable_thinking": bool(self.config.get("enable_thinking", False)),
            "n": 1,
            "size": self.normalized_size(image_config),
            "watermark": bool(self.config.get("watermark", False)),
        }
        negative_prompt = image_config.get("negative_prompt_override", self.config.get("negative_prompt"))
        if negative_prompt is not None:
            parameters["negative_prompt"] = negative_prompt
        if self.config.get("seed") is not None:
            parameters["seed"] = int(self.config["seed"])
        return {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }

    def submit_url(self):
        return f"{self.base_url}/api/v1/services/aigc/multimodal-generation/generation"

    def image_urls(self, response):
        urls = []
        for choice in (response.get("output") or {}).get("choices", []):
            for item in (choice.get("message") or {}).get("content") or []:
                if item.get("image"):
                    urls.append(item["image"])
        return urls
