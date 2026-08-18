"""Proxy routing/onboarding/wiring tests (isolated HOME + config dir, no network)."""

from __future__ import annotations

import io
import json

import pytest
import qwen_mm_plugins_proxy.capability as capability
import qwen_mm_plugins_proxy.onboarding as onboarding
import qwen_mm_plugins_proxy.wiring as wiring
from qwen_mm_plugins_proxy.config import (
    ConfigError,
    ProxyConfig,
    RelayConfig,
    RoutingConfig,
    load_config,
    save_config,
)


# ── config routing block ─────────────────────────────────────────────
def test_routing_config_parse_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path))
    p = tmp_path / "proxy.json"
    p.write_text(
        json.dumps(
            {
                "server": {"bind_port": 9000},
                "routing": {
                    "auto_wire": False,
                    "harnesses": ["claude"],
                    "capability_confirmed": True,
                    "unknown_default": "vision",
                    "relay_templates": {"r1": {"protocol": "chat", "base_url": "http://x"}},
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(str(p))
    assert cfg.routing.auto_wire is False
    assert cfg.routing.harnesses == ["claude"]
    assert cfg.routing.capability_confirmed is True
    assert cfg.routing.unknown_default == "vision"
    assert cfg.routing.relay_templates == {"r1": {"protocol": "chat", "base_url": "http://x"}}
    # save_config roundtrip 保留 routing
    save_config(cfg)
    cfg2 = load_config(str(p))
    assert cfg2.routing.auto_wire is False
    assert cfg2.routing.unknown_default == "vision"


def test_routing_bad_harness_and_unknown_default_raise():
    with pytest.raises(ConfigError):
        RoutingConfig(harnesses=["nope"])
    with pytest.raises(ConfigError):
        RoutingConfig(unknown_default="turbo")


def test_unknown_default_fallthrough():
    cfg = ProxyConfig(routing=RoutingConfig(unknown_default="vision"))
    assert capability.CapabilityTable()._resolve("zz-unknown-123", cfg) == "vision"
    cfg2 = ProxyConfig(routing=RoutingConfig(unknown_default="text_only"))
    assert capability.CapabilityTable()._resolve("zz-unknown-123", cfg2) == "text_only"


# ── wiring: backup / rewrite / restore (isolated HOME) ────────────────
def _mk_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    wiring.HOME = str(home)
    return home


def test_wiring_claude_rewrite_and_restore(tmp_path, monkeypatch):
    home = _mk_home(tmp_path)
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    claude_dir = home / ".claude"
    claude_dir.mkdir()
    f = claude_dir / "settings.json"
    f.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://real.example"}}), encoding="utf-8")
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=["claude"]))
    msg = wiring.wiring_backup_and_rewrite(cfg)
    assert any("claude" in m and "ok" in m for m in msg)
    d = json.loads(f.read_text(encoding="utf-8"))
    assert d["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8787"
    assert (claude_dir / "settings.json.qwen-mm-proxy.bak").exists()
    # 还原
    msg = wiring.wiring_restore(cfg)
    assert any("claude: restored" in m for m in msg)
    d = json.loads(f.read_text(encoding="utf-8"))
    assert d["env"]["ANTHROPIC_BASE_URL"] == "https://real.example"
    assert not (claude_dir / "settings.json.qwen-mm-proxy.bak").exists()


def test_wiring_restore_skips_non_proxy_value(tmp_path, monkeypatch):
    home = _mk_home(tmp_path)
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    claude_dir = home / ".claude"
    claude_dir.mkdir()
    f = claude_dir / "settings.json"
    f.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://real.example"}}), encoding="utf-8")
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=["claude"]))
    wiring.wiring_backup_and_rewrite(cfg)
    assert (claude_dir / "settings.json.qwen-mm-proxy.bak").exists()
    # 用户中途把 base_url 改走（模拟工具切换配置）
    f.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:15721"}}), encoding="utf-8")
    msg = wiring.wiring_restore(cfg)
    assert any("未还原(保留备份)" in m for m in msg)
    d = json.loads(f.read_text(encoding="utf-8"))
    assert d["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:15721"  # 不踩坏用户值


def test_wiring_missing_file_skips(tmp_path, monkeypatch):
    _mk_home(tmp_path)  # 副作用：设置 wiring.HOME
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig(bind_port=8787, routing=RoutingConfig(harnesses=["claude", "qwen-code"]))
    msg = wiring.wiring_backup_and_rewrite(cfg)
    assert any("skipped" in m for m in msg)


def test_wiring_json_dotted_and_toml_rewrite(tmp_path, monkeypatch):
    home = _mk_home(tmp_path)
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    # qwen-code 现在是 ~/.qwen/settings.json 的 model.baseUrl(点号嵌套 key)
    q = home / ".qwen"
    q.mkdir(parents=True)
    (q / "settings.json").write_text(
        json.dumps({"model": {"name": "mimo-v2.5", "baseUrl": "https://opencode.ai/zen/go/v1"}}), encoding="utf-8"
    )
    c = home / ".codex"
    c.mkdir()
    (c / "config.toml").write_text('model = "gpt-5-codex"\nbase_url = "https://codex.example"\n', encoding="utf-8")
    cfg = ProxyConfig(bind_port=9000, routing=RoutingConfig(harnesses=["qwen-code", "codex"]))
    wiring.wiring_backup_and_rewrite(cfg)
    d = json.loads((q / "settings.json").read_text(encoding="utf-8"))
    assert d["model"]["baseUrl"] == "http://127.0.0.1:9000"
    assert d["model"]["name"] == "mimo-v2.5"  # 其它字段不动
    toml = (c / "config.toml").read_text(encoding="utf-8")
    assert 'base_url = "http://127.0.0.1:9000"' in toml


# ── relays activate / restore ────────────────────────────────────────
def test_relays_activate_and_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    tmp_path.mkdir(exist_ok=True)
    cfg = ProxyConfig()
    cfg.routing.relay_templates = {
        "cc-claude": {
            "protocol": "anthropic",
            "base_url": "http://127.0.0.1:15721",
            "via": "cc-switch",
            "models": ["*"],
        }
    }
    msgs = wiring.relays_activate(cfg)
    assert any("已激活" in m for m in msgs)
    assert any(r.name == "cc-claude" for r in cfg.relays)
    assert cfg.routing.activated_relays == ["cc-claude"]
    msgs = wiring.relays_restore(cfg)
    assert any("已还原移除" in m for m in msgs)
    assert cfg.relays == []


def test_relays_activate_skips_existing_name(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig(relays=[RelayConfig(name="cc-claude", protocol="anthropic", base_url="http://a")])
    cfg.routing.relay_templates = {"cc-claude": {"protocol": "anthropic", "base_url": "http://a"}}
    wiring.relays_activate(cfg)
    assert len(cfg.relays) == 1  # 不重复加


# ── onboarding ────────────────────────────────────────────────────────
def _grp(g, path, models):
    return onboarding.ModelGroup(g, path, [onboarding.ModelEntry(m) for m in models])


def test_onboarding_confirm_default_text_only():
    groups = [_grp("claude", "cfg", ["deepseek-v4-flash", "glm-5.1"])]
    keys = iter(["enter"])  # 不切换 -> 全部 text_only
    cap = onboarding.confirm_models(groups, key_source=lambda: next(keys), out=io.StringIO())
    assert cap == {"claude": {"deepseek-v4-flash": "text_only", "glm-5.1": "text_only"}}


def test_onboarding_confirm_toggle_vision_second():
    groups = [_grp("claude", "cfg", ["minimax-m3", "deepseek-v4-flash"])]
    keys = iter(["down", "space", "enter"])  # 到第二个并切 vision
    cap = onboarding.confirm_models(groups, key_source=lambda: next(keys), out=io.StringIO())
    assert cap["claude"]["deepseek-v4-flash"] == "vision"
    assert cap["claude"]["minimax-m3"] == "text_only"


def test_onboarding_grouped_output_has_header():
    groups = [_grp("claude", "~/.claude/settings.json", ["sonnet"])]
    buf = io.StringIO()
    onboarding.confirm_models(groups, key_source=lambda: "enter", out=buf)
    assert "[claude]" in buf.getvalue() and "~/.claude/settings.json" in buf.getvalue()


def test_onboarding_run_writes_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    tmp_path.mkdir(exist_ok=True)
    cfg = ProxyConfig()
    cfg.routing.relay_templates = {"r1": {"protocol": "chat", "base_url": "http://x", "models": ["deepseek-*"]}}
    keys = iter(["enter"])
    assert onboarding.run_onboarding(cfg, key_source=lambda: next(keys), out=io.StringIO()) is True
    assert cfg.routing.capability_confirmed is True
    assert isinstance(cfg.model_capabilities.get("relay"), dict)  # 按组写入
    cfg2 = load_config()
    assert cfg2.routing.capability_confirmed is True
    assert onboarding.run_onboarding(cfg2) is True  # 已确认 -> 直接返回 True


def test_onboarding_cancel_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig()
    cfg.routing.relay_templates = {"r1": {"protocol": "chat", "base_url": "http://x", "models": ["a"]}}
    assert onboarding.run_onboarding(cfg, key_source=lambda: "q", out=io.StringIO()) is False


def test_scan_model_groups_partitions_by_harness(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    import qwen_mm_plugins_proxy.wiring as W

    home = tmp_path / "home"
    home.mkdir()
    W.HOME = str(home)
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(
        'model = "gpt-5-codex"\nbase_url = "https://codex.example"\n',
        encoding="utf-8",
    )
    (home / ".qwen").mkdir(parents=True)
    (home / ".qwen" / "settings.json").write_text(
        json.dumps({"model": {"name": "mimo-v2.5", "baseUrl": "https://opencode.ai/zen/go/v1"}}),
        encoding="utf-8",
    )
    cfg = ProxyConfig()
    cfg.routing.relay_templates = {
        "r1": {"protocol": "chat", "base_url": "http://127.0.0.1:15721", "models": ["deepseek-*"]}
    }
    groups = onboarding.scan_model_groups(cfg)
    names = {g.group for g in groups}
    assert {"codex", "qwen-code", "relay"} <= names
    codex_g = next(g for g in groups if g.group == "codex")
    assert any(e.model == "gpt-5-codex" and e.variable == "model" for e in codex_g.entries)
    qwen_g = next(g for g in groups if g.group == "qwen-code")
    assert any(e.model == "mimo-v2.5" for e in qwen_g.entries)


def test_judge_harness_aware():
    cfg = ProxyConfig(model_capabilities={"claude": {"sonnet": "text_only"}, "global": {"minimax-m3": "vision"}})
    t = capability.CapabilityTable()
    assert t.judge("sonnet", cfg, "claude") == "text_only"
    assert t.judge("minimax-m3", cfg, "codex") == "vision"  # 全局组兜底
    assert t.judge("zz-unknown", cfg, "codex") == cfg.routing.unknown_default


def test_onboarding_delta_prompts_only_new(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig()
    cfg.routing.relay_templates = {"r1": {"protocol": "chat", "base_url": "http://x", "models": ["deepseek-*"]}}
    assert onboarding.run_onboarding(cfg, key_source=lambda: "enter", out=io.StringIO()) is True  # 首次全量
    assert "deepseek-*" in onboarding._stored_models(cfg)
    # 新增一个模型族：再次 start 应只补新模型
    cfg.routing.relay_templates["r2"] = {"protocol": "chat", "base_url": "http://y", "models": ["glm-*"]}
    buf = io.StringIO()
    assert onboarding.run_onboarding(cfg, key_source=lambda: "enter", out=buf) is True
    assert "检测到新模型" in buf.getvalue()
    stored = onboarding._stored_models(cfg)
    assert "glm-*" in stored and "deepseek-*" in stored


def test_onboarding_no_new_models_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig()
    cfg.routing.relay_templates = {"r1": {"protocol": "chat", "base_url": "http://x", "models": ["aa"]}}
    assert onboarding.run_onboarding(cfg, key_source=lambda: "enter", out=io.StringIO()) is True
    buf = io.StringIO()
    assert onboarding.run_onboarding(cfg, key_source=lambda: "enter", out=buf) is True  # 无新模型
    assert "检测到新模型" not in buf.getvalue()


def test_edit_all_rewrites(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = ProxyConfig()
    cfg.routing.relay_templates = {"r1": {"protocol": "chat", "base_url": "http://x", "models": ["a"]}}
    assert onboarding.edit_all(cfg, key_source=lambda: "enter", out=io.StringIO()) is True
    assert isinstance(cfg.model_capabilities.get("relay"), dict)
    assert "a" in cfg.model_capabilities["relay"]
