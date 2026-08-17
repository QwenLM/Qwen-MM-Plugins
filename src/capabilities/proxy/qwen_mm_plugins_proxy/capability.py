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
        self._cache: dict[str, str] = {}

    def judge(self, model: str, cfg: ProxyConfig) -> str:
        if model in self._cache:
            return self._cache[model]
        capability = self._resolve(model, cfg)
        self._cache[model] = capability
        return capability

    @staticmethod
    def _resolve(model: str, cfg: ProxyConfig) -> str:
        # 1. 用户显式配置（精确模型名、前缀、通配符，顺序匹配命中即止）
        for pattern, cap in cfg.model_capabilities.items():
            if fnmatch.fnmatch(model, pattern):
                return cap
        # 2. 内置名单
        for pattern, cap in BUILTIN_CAPABILITIES.items():
            if fnmatch.fnmatch(model, pattern):
                return cap
        # 3. 未知 -> 默认拦截（text_only，走一次 VLM）
        return "text_only"
