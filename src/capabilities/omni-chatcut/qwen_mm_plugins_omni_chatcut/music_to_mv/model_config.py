"""Shared model connection configuration for Music-to-MV runtimes and tools."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from shared.env import get_env

MODEL_CONFIG_ENV = "QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG"


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_model_config_path(path: str | Path | None = None) -> Path | None:
    """Resolve an explicit path, then the single shared config-path environment variable."""
    selected = str(path).strip() if path else (get_env(MODEL_CONFIG_ENV, "", refresh_config=True) or "").strip()
    return Path(selected).expanduser().resolve() if selected else None


def load_model_config(path: str | Path | None = None) -> tuple[dict[str, Any], Path | None]:
    """Load the unified connection file without ever accepting inline credentials."""
    resolved = resolve_model_config_path(path)
    if resolved is None:
        return {}, None
    if not resolved.is_file():
        raise FileNotFoundError(f"model config is not a readable file: {resolved}")
    with resolved.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"model config root must be a JSON object: {resolved}")
    _reject_inline_api_keys(config, resolved)
    for section in ("omni", "image_providers", "video_providers", "providers"):
        if section in config and not isinstance(config[section], dict):
            raise ValueError(f"model config {section} must be a JSON object: {resolved}")
    return config, resolved


def pipeline_model_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Translate the public video_providers name to the executor's legacy providers table."""
    result: dict[str, Any] = {}
    if isinstance(config.get("image_providers"), dict):
        result["image_providers"] = copy.deepcopy(config["image_providers"])
    legacy_video = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    public_video = config.get("video_providers") if isinstance(config.get("video_providers"), dict) else {}
    if legacy_video or public_video:
        result["providers"] = deep_merge(legacy_video, public_video)
    return result


def _reject_inline_api_keys(value: Any, path: Path, prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            field = f"{prefix}.{key}" if prefix else str(key)
            if key in {"api_key", "key", "token", "access_token"}:
                raise ValueError(
                    f"model config must name credential environment variables, not store credentials: {field} in {path}"
                )
            _reject_inline_api_keys(child, path, field)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_inline_api_keys(child, path, f"{prefix}[{index}]")
