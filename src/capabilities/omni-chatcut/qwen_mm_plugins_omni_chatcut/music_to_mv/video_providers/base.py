"""Provider-neutral contracts for asynchronous video generation."""

from __future__ import annotations

import math


class VideoProvider:
    name = "base"
    schema_version = "video-provider/v1"
    reference_audio_transport = "remote_url"

    def __init__(self, config):
        self.config = dict(config or {})
        self.base_url = str(self.config.get("base_url", "")).rstrip("/")
        self.api_key_env = self.config.get("api_key_env", "")
        self.model = self.config.get("model") or self.config.get("video_model") or ""

    def validate_configuration(self):
        if not self.base_url:
            raise ValueError(f"providers.{self.name}.base_url is required")
        if not self.api_key_env:
            raise ValueError(f"providers.{self.name}.api_key_env is required")
        if not self.model:
            raise ValueError(f"providers.{self.name}.model is required")

    def render_prompt(self, prompt):
        return prompt

    def signature_parameters(self, video_config):
        return {
            "provider": self.name,
            "provider_schema": self.schema_version,
            "base_url": self.base_url,
            "model": self.model,
            "resolution": video_config.get("resolution"),
            "ratio": video_config.get("ratio"),
        }

    def validate_request(self, record, image_urls, audio_url, video_config):
        self.validate_configuration()

    def storyboard_errors(self, segments):
        return []

    def api_duration(self, storyboard_duration_sec):
        return max(2, math.ceil(float(storyboard_duration_sec)))

    def validate_local_identity(self, path):
        return None

    def identity_reference_url(self, reference, local_data_url):
        return local_data_url

    def build_payload(self, record, identity_urls, audio_url, video_config, validate=True):
        raise NotImplementedError

    def submit_url(self):
        raise NotImplementedError

    def query_url(self, task_id):
        raise NotImplementedError

    def submit_headers(self, payload):
        return {}

    def persisted_payload(self, payload):
        return payload

    def query_headers(self):
        return {}

    def http_succeeded(self, status):
        return 200 <= int(status) < 300

    def task_id(self, response):
        raise NotImplementedError

    def remote_status(self, response):
        raise NotImplementedError

    def video_url(self, response):
        raise NotImplementedError

    def succeeded(self, status):
        return status == "succeeded"

    def failed(self, status):
        return status in {"failed", "cancelled", "canceled", "expired", "unknown"}


def canonical_video_provider_name(name):
    normalized = str(name or "wan3").lower().replace("_", "").replace("-", "").replace(".", "")
    if normalized in {"wan3", "wan30"}:
        return "wan3"
    if normalized in {"seedance", "doubaoseedance", "seedance25", "doubaoseedance25"}:
        return "seedance"
    return str(name or "wan3").lower()


def create_video_provider(name, config):
    # Imports stay local so adapters can import the base class without a cycle.
    from .seedance import SeedanceProvider
    from .wan30 import Wan30Provider

    canonical = canonical_video_provider_name(name)
    if canonical == "wan3":
        return Wan30Provider(config)
    if canonical == "seedance":
        return SeedanceProvider(config)
    raise ValueError(f"unsupported video.provider: {name}")
