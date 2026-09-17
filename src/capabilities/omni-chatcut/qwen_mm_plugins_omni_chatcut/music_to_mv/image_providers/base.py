"""Provider-neutral contracts for synchronous image generation."""

from __future__ import annotations

from pathlib import Path


class ImageProvider:
    name = "base"
    schema_version = "image-provider/v1"
    output_suffix = None
    result_url_ttl_sec = None

    def __init__(self, config):
        self.config = dict(config or {})
        self.base_url = str(self.config.get("base_url", "")).rstrip("/")
        self.api_key_env = self.config.get("api_key_env", "")
        self.model = self.config.get("model") or self.config.get("image_model") or ""

    def validate_configuration(self):
        if not self.base_url:
            raise ValueError(f"image_providers.{self.name}.base_url is required")
        if not self.api_key_env:
            raise ValueError(f"image_providers.{self.name}.api_key_env is required")
        if not self.model:
            raise ValueError(f"image_providers.{self.name}.model is required")

    def resolve_size(self, image_config):
        return image_config.get("size_override") or self.config.get("size") or image_config.get("size")

    def max_attempts(self, image_config):
        return int(self.config.get("max_attempts", image_config.get("max_attempts", 3)))

    def workers(self, image_config):
        return int(self.config.get("workers", image_config.get("base_workers", 1)))

    def signature_parameters(self, image_config):
        return {
            "provider": self.name,
            "provider_schema": self.schema_version,
            "base_url": self.base_url,
            "model": self.model,
            "size": self.resolve_size(image_config),
        }

    def output_path(self, requested_path):
        path = Path(requested_path)
        return path.with_suffix(self.output_suffix) if self.output_suffix else path

    def build_payload(self, prompt, image_urls, image_config):
        raise NotImplementedError

    def submit_url(self):
        raise NotImplementedError

    def submit_headers(self, payload):
        return {}

    def image_urls(self, response):
        raise NotImplementedError


def canonical_image_provider_name(name):
    normalized = str(name or "qwen_image_3").lower().replace("_", "").replace("-", "").replace(".", "")
    if normalized in {"qwenimage3", "qwenimage30", "qwenimage3pro", "qwenimage30pro"}:
        return "qwen_image_3"
    if normalized in {"seedream", "seedream45", "doubaoseedream45", "doubaoseedream45251128"}:
        return "seedream"
    return str(name or "qwen_image_3").lower()


def create_image_provider(name, config):
    # Imports stay local so adapters can import the base class without a cycle.
    from .qwen_image30 import QwenImage30Provider
    from .seedream import SeedreamProvider

    canonical = canonical_image_provider_name(name)
    if canonical == "qwen_image_3":
        return QwenImage30Provider(config)
    if canonical == "seedream":
        return SeedreamProvider(config)
    raise ValueError(f"unsupported image.provider: {name}")
