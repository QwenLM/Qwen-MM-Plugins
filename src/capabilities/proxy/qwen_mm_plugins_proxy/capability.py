"""Model capability judgment: user config > builtin list > unknown defaults to intercept."""

from __future__ import annotations

import fnmatch

from .config import ProxyConfig

BUILTIN_CAPABILITIES: dict[str, str] = {
    "deepseek/*": "text_only",
    "glm/*": "text_only",
    "zai/*": "text_only",
    "openai/*": "vision",
    "anthropic/*": "vision",
    "google/*": "vision",
    "qwen-vl-*": "vision",
    "qwen3.5-omni-*": "vision",
    "kimi-k2.7-code*": "vision",
    "openrouter/deepseek/*": "text_only",
}


class CapabilityTable:
    def __init__(self) -> None:
        self._cache: dict[tuple, str] = {}

    def judge(self, model: str, cfg: ProxyConfig, harness: str | None = None) -> str:
        key = (harness, model)
        if key in self._cache:
            return self._cache[key]
        capability = self._resolve(model, cfg, harness)
        self._cache[key] = capability
        return capability

    @staticmethod
    def _resolve(model: str, cfg: ProxyConfig, harness: str | None = None) -> str:
        caps = cfg.model_capabilities
        # 1a. 按 harness 分组嵌套：先本组，再 global（旧扁平迁移后的兜底组）
        if harness and isinstance(caps.get(harness), dict) and model in caps[harness]:
            return caps[harness][model]
        if isinstance(caps.get("global"), dict) and model in caps["global"]:
            return caps["global"][model]
        # 1b. 旧扁平 map{model: cap}（未迁移前的兼容），顺序匹配命中即止
        for pattern, cap in caps.items():
            if isinstance(cap, str) and fnmatch.fnmatch(model, pattern):
                return cap
        # 2. 内置名单
        for pattern, cap in BUILTIN_CAPABILITIES.items():
            if fnmatch.fnmatch(model, pattern):
                return cap
        # 3. 未知 -> 默认（text_only 安全；可配 routing.unknown_default=vision）
        return cfg.routing.unknown_default
