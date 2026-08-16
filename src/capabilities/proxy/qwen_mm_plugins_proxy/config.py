"""Proxy configuration: JSON file (~/.qwen-mm-plugins/proxy.json, 0600) + env overrides.

Spec says proxy.toml; the repo floor is Python 3.10 (no guaranteed tomllib), so we use JSON
(see plan Global Constraints). Read via shared.env.get_env for env overrides.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from shared.env import get_env

VLM_FORMATS = ("chat", "anthropic")


@dataclass
class VLMConfig:
    model: str = "qwen-vl-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    format: str = "chat"  # chat | anthropic（responses 留二阶段）
    cache_disk: bool = False
    auto_local_ollama: bool = True
    timeout_ms: int = 120_000
    max_tokens: int = 4096


@dataclass
class RelayConfig:
    name: str
    protocol: str  # anthropic | responses | chat
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)
    capability: str | None = None  # 显式覆盖能力判定


@dataclass
class ProxyConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787
    ui_port: int = 8788
    relays: list[RelayConfig] = field(default_factory=list)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    model_capabilities: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        server = data.get("server", {})
        vlm = data.get("vlm", {})
        return cls(
            bind_host=server.get("bind_host", "127.0.0.1"),
            bind_port=int(server.get("bind_port", 8787)),
            ui_port=int(server.get("ui_port", 8788)),
            relays=[RelayConfig(**r) for r in data.get("relays", [])],
            vlm=VLMConfig(**{k: v for k, v in vlm.items() if k in VLMConfig.__dataclass_fields__}),
            model_capabilities=data.get("model_capabilities", {}),
        )

    def to_dict(self) -> dict:
        return {
            "server": {"bind_host": self.bind_host, "bind_port": self.bind_port, "ui_port": self.ui_port},
            "relays": [r.__dict__ for r in self.relays],
            "vlm": self.vlm.__dict__,
            "model_capabilities": self.model_capabilities,
        }


def default_config() -> ProxyConfig:
    return ProxyConfig.from_dict({})


def _apply_env(cfg: ProxyConfig) -> ProxyConfig:
    """Env overrides (QWEN_MM_PROXY_*), applied at load time via shared.env.get_env."""
    if v := get_env("QWEN_MM_PROXY_BIND_PORT"):
        cfg.bind_port = int(v)
    if v := get_env("QWEN_MM_PROXY_VLM_MODEL"):
        cfg.vlm.model = v
    if v := get_env("QWEN_MM_PROXY_VLM_BASE_URL"):
        cfg.vlm.base_url = v
    if v := get_env("QWEN_MM_PROXY_VLM_API_KEY"):
        cfg.vlm.api_key = v
    if v := get_env("QWEN_MM_PROXY_VLM_FORMAT"):
        if v in VLM_FORMATS:
            cfg.vlm.format = v
    return cfg


def load_config(path: str | None = None) -> ProxyConfig:
    if path is None:
        from shared.env import config_dir

        path = os.path.join(config_dir(), "proxy.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = ProxyConfig.from_dict(json.load(f))
    except (OSError, ValueError):
        cfg = default_config()
    return _apply_env(cfg)
