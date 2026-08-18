from __future__ import annotations

from qwen_mm_plugins_proxy.capability import CapabilityTable
from qwen_mm_plugins_proxy.config import ProxyConfig

BUILTIN = {
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


def test_builtin_list_matches_spec():
    table = CapabilityTable()
    assert table.judge("deepseek/deepseek-chat", ProxyConfig()) == "text_only"
    assert table.judge("qwen-vl-max", ProxyConfig()) == "vision"
    assert table.judge("openrouter/deepseek/v3", ProxyConfig()) == "text_only"


def test_user_config_overrides_builtin():
    cfg = ProxyConfig(model_capabilities={"deepseek-vl-2": "vision"})
    assert CapabilityTable().judge("deepseek-vl-2", cfg) == "vision"


def test_unknown_model_defaults_to_intercept():
    assert CapabilityTable().judge("mystery-model", ProxyConfig()) == "text_only"
