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

# 已知的本地路由工具（两层拓扑）与其默认端口。via 只是描述性标签＋供 check 校验，
# 不参与 URL 拼接——URL 始终由 base_url 决定（回环=经工具两层、远端=直连一层）。
VIA_TOOLS = {"cc-switch": 15721, "codex-plus": 57321}

# start/stop 自动接线覆盖的 harness（第一跳 base_url→本代理）。
HARNESSES = ("claude", "codex", "qwen-code")


@dataclass
class RoutingConfig:
    """自动接线与路由的开关/状态。"""

    auto_wire: bool = True  # start 自动改三处 harness base_url→本代理、stop 自动还原
    harnesses: list[str] = field(default_factory=lambda: list(HARNESSES))  # 可排除任一
    relay_templates: dict[str, dict] = field(default_factory=dict)  # name -> {protocol,base_url,via?,models[]}
    capability_confirmed: bool = False  # 首次是否已显式确认过各模型看图能力
    unknown_default: str = "text_only"  # 未归类模型默认：text_only(安全) | vision
    activated_relays: list[str] = field(default_factory=list)  # start 激活、stop 还原的 relay name 记录

    def __post_init__(self) -> None:
        bad = [h for h in self.harnesses if h not in HARNESSES]
        if bad:
            raise ConfigError(
                f"routing.harnesses: unknown harness {','.join(map(repr, bad))}; must be in {list(HARNESSES)}"
            )
        if self.unknown_default not in ("text_only", "vision"):
            raise ConfigError(f"routing.unknown_default must be 'text_only' or 'vision', got {self.unknown_default!r}")


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
    via: str | None = None  # 可选：本 relay 是否经本地路由工具转发(两层拓扑)。纯描述性，不参与 URL 拼接。

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ConfigError(
                f"relay {self.name!r}: protocol must be one of {', '.join(PROTOCOLS)}, got {self.protocol!r}"
            )
        if self.via is not None and self.via not in VIA_TOOLS:
            raise ConfigError(
                f"relay {self.name!r}: via must be one of {sorted(VIA_TOOLS)} (or omitted), got {self.via!r}"
            )


@dataclass
class ProxyConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787
    ui_port: int = 8788
    relays: list[RelayConfig] = field(default_factory=list)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    model_capabilities: dict[str, str] = field(default_factory=dict)
    routing: RoutingConfig = field(default_factory=RoutingConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        server = data.get("server", {})
        vlm = data.get("vlm", {})
        routing = data.get("routing", {})
        return cls(
            bind_host=server.get("bind_host", "127.0.0.1"),
            bind_port=int(server.get("bind_port", 8787)),
            ui_port=int(server.get("ui_port", 8788)),
            relays=_parse_relays(data.get("relays", [])),
            vlm=VLMConfig(**{k: v for k, v in vlm.items() if k in VLMConfig.__dataclass_fields__}),
            model_capabilities=data.get("model_capabilities", {}),
            routing=RoutingConfig(**{k: v for k, v in routing.items() if k in RoutingConfig.__dataclass_fields__}),
        )

    def to_dict(self) -> dict:
        return {
            "server": {"bind_host": self.bind_host, "bind_port": self.bind_port, "ui_port": self.ui_port},
            "relays": [r.__dict__ for r in self.relays],
            "vlm": self.vlm.__dict__,
            "model_capabilities": self.model_capabilities,
            "routing": self.routing.__dict__,
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


def save_config(cfg: ProxyConfig, path: str | None = None) -> str:
    """把 cfg 原子写回 proxy.json（0600）。onboarding/生命周期写入用它持久化。"""
    if path is None:
        from shared.env import config_dir

        path = os.path.join(config_dir(), "proxy.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


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
