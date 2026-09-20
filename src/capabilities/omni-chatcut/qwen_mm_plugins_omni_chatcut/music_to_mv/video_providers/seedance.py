"""Official Volcengine Seedance asynchronous video adapter."""

from __future__ import annotations

import copy
import hashlib
import re

from .base import VideoProvider


class SeedanceProvider(VideoProvider):
    name = "seedance"
    schema_version = "seedance-official-presets/v2"
    reference_audio_transport = "inline_data_url"
    RESOLUTIONS = {"480p", "720p", "1080p"}
    RATIOS = {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}

    @staticmethod
    def resolve_ratio(video_config):
        ratio = video_config.get("ratio")
        return "16:9" if not ratio or ratio == "adaptive" else ratio

    def signature_parameters(self, video_config):
        values = super().signature_parameters(video_config)
        values.update(
            {
                "resolution": str(video_config.get("resolution", "1080p")).lower(),
                "ratio": self.resolve_ratio(video_config),
                "generate_audio": bool(self.config.get("generate_audio", False)),
                "watermark": bool(self.config.get("watermark", False)),
                "official_identity_assets": self.config.get("official_identity_assets") or {},
            }
        )
        return values

    def storyboard_errors(self, segments):
        errors = []
        try:
            self.validate_configuration()
        except ValueError as exc:
            errors.append(str(exc))
        for cast_id in sorted({cast_id for segment in segments for cast_id in segment.get("cast_present", [])}):
            try:
                self.identity_reference_url({"cast_id": cast_id}, None)
            except ValueError as exc:
                errors.append(str(exc))
        for segment in segments:
            index = int(segment["index"])
            duration = float(segment["duration_sec"])
            if duration > 13:
                errors.append(
                    f"shot {index}: storyboard duration {duration:g}s exceeds the supported 13s normalization ceiling"
                )
        return errors

    def api_duration(self, storyboard_duration_sec):
        return min(12, max(4, super().api_duration(storyboard_duration_sec)))

    def validate_configuration(self):
        super().validate_configuration()
        if "identity_reference_urls" in self.config or "register_identity_assets" in self.config:
            raise ValueError(
                "Legacy Seedance identity configuration is unsupported; select existing Ark image assets "
                "and configure providers.seedance.official_identity_assets. Asset registration is removed."
            )
        if not isinstance(self.config.get("official_identity_assets", {}), dict):
            raise ValueError("providers.seedance.official_identity_assets must map cast IDs to asset:// IDs")

    @staticmethod
    def is_official_asset_uri(value):
        # Accept public or user-uploaded Ark image assets. The API checks access;
        # URI syntax cannot prove catalog provenance or authorization offline.
        return isinstance(value, str) and re.fullmatch(r"asset://asset-[A-Za-z0-9-]+", value) is not None

    def identity_reference_url(self, reference, local_data_url):
        self.validate_configuration()
        cast_id = str(reference.get("cast_id", ""))
        value = (self.config.get("official_identity_assets") or {}).get(cast_id)
        if not self.is_official_asset_uri(value):
            raise ValueError(
                f"Seedance character {cast_id!r} requires an Ark image asset://asset-... ID in "
                "providers.seedance.official_identity_assets; generated portraits, image URLs, local "
                "images, and unselected legacy assets are not used as fallbacks."
            )
        return value

    def validate_request(self, record, image_urls, audio_url, video_config):
        super().validate_request(record, image_urls, audio_url, video_config)
        duration = int(record["api_duration_sec"])
        if not 4 <= duration <= 12:
            raise ValueError(f"Seedance reference-video duration must be an integer from 4 through 12; got {duration}")
        resolution = str(video_config.get("resolution", "1080p")).lower()
        if resolution not in self.RESOLUTIONS:
            raise ValueError(f"Seedance resolution must be one of {sorted(self.RESOLUTIONS)}; got {resolution}")
        ratio = self.resolve_ratio(video_config)
        if ratio not in self.RATIOS:
            raise ValueError(f"Seedance ratio must be one of {sorted(self.RATIOS)}; got {ratio}")
        if not str(audio_url or "").startswith("data:audio/"):
            raise ValueError("Seedance reference audio must be an inline audio data URL")
        invalid_images = [url for url in image_urls if not self.is_official_asset_uri(url)]
        if invalid_images:
            raise ValueError("Seedance identity references must use Ark image asset://asset-... IDs")
        if not record.get("video_prompt", "").strip():
            raise ValueError("Seedance prompt cannot be empty")

    def build_payload(self, record, identity_urls, audio_url, video_config, validate=True):
        if any(not self.is_official_asset_uri(url) for url in identity_urls):
            raise ValueError("Seedance identity references must use Ark image asset://asset-... IDs")
        if validate:
            self.validate_request(record, identity_urls, audio_url, video_config)
        content = [{"type": "text", "text": record["video_prompt"].strip()}]
        content.extend(
            {
                "type": "image_url",
                "role": "reference_image",
                "image_url": {"url": url},
            }
            for url in identity_urls
        )
        content.append(
            {
                "type": "audio_url",
                "role": "reference_audio",
                "audio_url": {"url": audio_url},
            }
        )
        return {
            "model": self.model,
            "content": content,
            "generate_audio": bool(self.config.get("generate_audio", False)),
            "resolution": str(video_config.get("resolution", "1080p")).lower(),
            "ratio": self.resolve_ratio(video_config),
            "duration": int(record["api_duration_sec"]),
            "watermark": bool(self.config.get("watermark", False)),
        }

    @staticmethod
    def _redact_data_url(value):
        if not isinstance(value, str) or not value.startswith("data:"):
            return value
        header, _, encoded = value.partition(",")
        digest = hashlib.sha256(encoded.encode("ascii")).hexdigest()
        return f"@inline:{header[5:]};sha256={digest}"

    def persisted_payload(self, payload):
        saved = copy.deepcopy(payload)
        for item in saved.get("content", []):
            if item.get("type") == "image_url":
                image = item.get("image_url") or {}
                image["url"] = self._redact_data_url(image.get("url"))
            elif item.get("type") == "audio_url":
                audio = item.get("audio_url") or {}
                audio["url"] = self._redact_data_url(audio.get("url"))
        return saved

    def submit_url(self):
        return f"{self.base_url}/api/v3/contents/generations/tasks"

    def query_url(self, task_id):
        return f"{self.submit_url()}/{task_id}"

    def task_id(self, response):
        return response.get("id") or (response.get("output") or {}).get("task_id", "")

    def remote_status(self, response):
        return str(response.get("status") or (response.get("output") or {}).get("task_status") or "unknown").lower()

    def video_url(self, response):
        content = response.get("content") or {}
        url = content.get("video_url") if isinstance(content, dict) else ""
        return url or (response.get("output") or {}).get("video_url", "")

    def failed(self, status):
        return status in {"failed", "canceled", "cancelled", "expired", "unknown"}
