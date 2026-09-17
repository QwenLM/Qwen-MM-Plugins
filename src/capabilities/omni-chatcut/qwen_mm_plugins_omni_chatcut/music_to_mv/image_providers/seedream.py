"""Official Volcengine Seedream synchronous image adapter."""

from __future__ import annotations

from .base import ImageProvider


class SeedreamProvider(ImageProvider):
    name = "seedream"
    schema_version = "seedream-4.5/v1"
    output_suffix = ".jpg"

    def normalized_size(self, image_config):
        value = str(self.resolve_size(image_config) or "").strip()
        if not value:
            raise ValueError("Seedream size is required, for example 2K")
        return value

    def signature_parameters(self, image_config):
        values = super().signature_parameters(image_config)
        values.update(
            {
                "size": self.normalized_size(image_config),
                "response_format": "url",
                "stream": False,
                "watermark": bool(self.config.get("watermark", False)),
            }
        )
        if self.config.get("seed") is not None:
            values["seed"] = int(self.config["seed"])
        return values

    def validate_request(self, image_urls, image_config):
        self.validate_configuration()
        self.normalized_size(image_config)
        if image_urls:
            raise ValueError("Music2MV Seedream identity generation currently supports text prompts only")

    def build_payload(self, prompt, image_urls, image_config):
        image_urls = list(image_urls or [])
        self.validate_request(image_urls, image_config)
        payload = {
            "model": self.model,
            "prompt": str(prompt).strip(),
            "response_format": "url",
            "size": self.normalized_size(image_config),
            "stream": False,
            "watermark": bool(self.config.get("watermark", False)),
        }
        if not payload["prompt"]:
            raise ValueError("Seedream prompt cannot be empty")
        if self.config.get("seed") is not None:
            payload["seed"] = int(self.config["seed"])
        return payload

    def submit_url(self):
        return f"{self.base_url}/api/v3/images/generations"

    def image_urls(self, response):
        return [item["url"] for item in (response.get("data") or []) if isinstance(item, dict) and item.get("url")]
