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

# relay 上游转发协议枚举：配置 proxy.json 时 protocol 字段只能是这三个之一（强校验）。
PROTOCOLS = ("anthropic", "responses", "chat")


class ConfigError(Exception):
    """proxy.json 配置非法（显式错误，不静默回退默认配置）。"""


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

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ConfigError(
                f"relay {self.name!r}: protocol must be one of {', '.join(PROTOCOLS)}, got {self.protocol!r}"
            )


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
            relays=_parse_relays(data.get("relays", [])),
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


def _parse_relays(raw: object) -> list[RelayConfig]:
    """逐条解析 relays；任一配置非法时抛带定位信息的 ConfigError（而非静默回退）。"""
    if not isinstance(raw, list):
        raise ConfigError(f"relays: expected a list, got {type(raw).__name__}")
    relays: list[RelayConfig] = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ConfigError(f"relays[{i}]: expected an object, got {type(r).__name__}")
        name = r.get("name", "<unnamed>")
        try:
            relays.append(RelayConfig(**r))
        except ConfigError as exc:
            raise ConfigError(f"relays[{i}]: {exc}") from exc
        except TypeError as exc:
            raise ConfigError(f"relays[{i}] ({name}): {exc}") from exc
    return relays


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
    # API key 用 is not None 而非真值判断：环境变量显式设为空串也必须清掉配置里的 key。
    # 否则 walrus 写法会把空串 '' 当 falsy 跳过，导致 T7 这类"拔 VLM key 测 fail-open"永远失效
    # （proxy.json 里的 key 原样保留，VLM 照常被调用）。
    vlm_key = get_env("QWEN_MM_PROXY_VLM_API_KEY")
    if vlm_key is not None:
        cfg.vlm.api_key = vlm_key
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
            raw = json.load(f)
    except FileNotFoundError:
        # 首次运行无配置文件：回退默认（合法），由 check 提示未配置。
        return default_config()
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read proxy.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"proxy.json: expected a JSON object at top level, got {type(raw).__name__}")
    try:
        cfg = ProxyConfig.from_dict(raw)
    except ConfigError:
        raise
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigError(f"invalid proxy.json: {exc}") from exc
    return _apply_env(cfg)
