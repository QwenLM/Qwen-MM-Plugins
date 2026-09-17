"""Alibaba Cloud Model Studio Wan 3.0 asynchronous video adapter."""

from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import urlparse

from .base import VideoProvider


class Wan30Provider(VideoProvider):
    name = "wan3"
    schema_version = "wan3-video/v1"
    RESOLUTIONS = {"480P", "720P", "1080P"}
    RATIOS = {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"}

    def __init__(self, config):
        super().__init__(config)
        workspace_id = self.config.get("workspace_id", "")
        if workspace_id:
            self.base_url = self.base_url.replace("{workspace_id}", str(workspace_id))

    def validate_configuration(self):
        super().validate_configuration()
        if "{" in self.base_url or "}" in self.base_url or "YOUR_WORKSPACE_ID" in self.base_url:
            raise ValueError(
                "providers.wan3.base_url contains an unresolved workspace placeholder; "
                "configure workspace_id/workspace_id_env or use https://dashscope.aliyuncs.com"
            )

    def storyboard_errors(self, segments):
        errors = []
        for segment in segments:
            index = int(segment["index"])
            duration = float(segment["duration_sec"])
            api_duration = max(2, math.ceil(duration))
            if not 2 <= api_duration <= 30:
                errors.append(f"shot {index}: output duration {api_duration}s is outside Wan's 2-30s range")
            if not 1 <= duration <= 15:
                errors.append(f"shot {index}: reference audio duration {duration}s is outside Wan's 1-15s range")
            identity_count = len(segment.get("cast_present", []))
            if identity_count > 10:
                errors.append(f"shot {index}: {identity_count} identity references exceed Wan's 10-image limit")
        return errors

    def validate_local_identity(self, path):
        path = Path(path)
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError(f"Wan 3.0 reference image exceeds 20MB: {path}")
        from PIL import Image

        with Image.open(path) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
        if image_format not in {"JPEG", "PNG", "BMP", "WEBP"}:
            raise ValueError(f"Wan 3.0 does not accept {image_format or 'unknown'} images: {path}")
        if min(width, height) < 240 or max(width, height) > 8000:
            raise ValueError(f"Wan 3.0 image sides must be within 240-8000px: {path} is {width}x{height}")
        if max(width, height) / min(width, height) > 8:
            raise ValueError(f"Wan 3.0 image aspect ratio must not exceed 8:1: {path}")
        if has_alpha:
            raise ValueError(f"Wan 3.0 reference images cannot contain transparency: {path}")

    def render_prompt(self, prompt):
        return prompt.replace("参考图片", "图").replace("完整输入音频", "音频1（完整分段音乐）")

    def signature_parameters(self, video_config):
        values = super().signature_parameters(video_config)
        values.update(
            {
                "resolution": str(video_config.get("resolution", "1080P")).upper(),
                "ratio": video_config.get("ratio") or "adaptive",
                "audio": bool(self.config.get("audio", False)),
                "prompt_extend": bool(self.config.get("prompt_extend", False)),
                "watermark": bool(self.config.get("watermark", False)),
                "seed": self.config.get("seed"),
            }
        )
        return values

    @staticmethod
    def _is_remote_audio_url(value):
        parsed = urlparse(str(value or ""))
        return parsed.scheme in {"http", "https", "oss"} and bool(parsed.netloc or parsed.path)

    def validate_request(self, record, image_urls, audio_url, video_config):
        super().validate_request(record, image_urls, audio_url, video_config)
        duration = int(record["api_duration_sec"])
        if not 2 <= duration <= 30:
            raise ValueError(f"Wan 3.0 duration must be an integer from 2 to 30 seconds; got {duration}")
        reference_audio_duration = float(record.get("duration_sec", duration))
        if not 1 <= reference_audio_duration <= 15:
            raise ValueError(f"Wan 3.0 reference audio must be from 1 to 15 seconds; got {reference_audio_duration}")
        resolution = str(video_config.get("resolution", "1080P")).upper()
        if resolution not in self.RESOLUTIONS:
            raise ValueError(f"Wan 3.0 resolution must be one of {sorted(self.RESOLUTIONS)}; got {resolution}")
        ratio = video_config.get("ratio") or "adaptive"
        if ratio not in self.RATIOS:
            raise ValueError(f"Wan 3.0 ratio must be one of {sorted(self.RATIOS)}; got {ratio}")
        if len(image_urls) > 10:
            raise ValueError(f"Wan 3.0 accepts at most 10 reference images; got {len(image_urls)}")
        if len(image_urls) + 1 > 20:
            raise ValueError("Wan 3.0 accepts at most 20 media inputs")
        if not self._is_remote_audio_url(audio_url):
            raise ValueError("Wan 3.0 reference_audio must be an http(s) or oss:// URL")
        if len(record["video_prompt"]) > 20000:
            raise ValueError("Wan 3.0 prompt exceeds the 20,000-character limit")
        seed = self.config.get("seed")
        if seed is not None and not 0 <= int(seed) <= 2147483647:
            raise ValueError("Wan 3.0 seed must be between 0 and 2147483647")

    def build_payload(self, record, identity_urls, audio_url, video_config, validate=True):
        all_images = list(identity_urls)
        if validate:
            self.validate_request(record, all_images, audio_url, video_config)
        media = [{"type": "reference_image", "url": url} for url in all_images]
        media.append({"type": "reference_audio", "url": audio_url})
        parameters = {
            "resolution": str(video_config.get("resolution", "1080P")).upper(),
            "ratio": video_config.get("ratio") or "adaptive",
            "duration": int(record["api_duration_sec"]),
            "audio": bool(self.config.get("audio", False)),
            "prompt_extend": bool(self.config.get("prompt_extend", False)),
            "watermark": bool(self.config.get("watermark", False)),
        }
        if self.config.get("seed") is not None:
            parameters["seed"] = int(self.config["seed"])
        return {
            "model": self.model,
            "input": {"prompt": record["video_prompt"], "media": media},
            "parameters": parameters,
        }

    def submit_url(self):
        return f"{self.base_url}/api/v1/services/aigc/video-generation/video-synthesis"

    def query_url(self, task_id):
        return f"{self.base_url}/api/v1/tasks/{task_id}"

    def submit_headers(self, payload):
        headers = {"X-DashScope-Async": "enable"}
        urls = [item.get("url", "") for item in (payload.get("input") or {}).get("media", [])]
        if any(str(url).startswith("oss://") for url in urls):
            headers["X-DashScope-OssResourceResolve"] = "enable"
        return headers

    def task_id(self, response):
        return (response.get("output") or {}).get("task_id", "")

    def remote_status(self, response):
        return str((response.get("output") or {}).get("task_status") or "UNKNOWN").lower()

    def video_url(self, response):
        return (response.get("output") or {}).get("video_url", "")

    def failed(self, status):
        return status in {"failed", "canceled", "cancelled", "unknown"}
