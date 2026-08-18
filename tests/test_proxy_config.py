"""Proxy capability: config load/save + env override + defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qwen_mm_plugins_proxy import __version__
from qwen_mm_plugins_proxy.cli import main as cli_main
from qwen_mm_plugins_proxy.config import (
    PROTOCOLS,
    ConfigError,
    ProxyConfig,
    RelayConfig,
    default_config,
    load_config,
)


def test_default_config_defaults():
    cfg = default_config()
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 8787
    assert cfg.ui_port == 8788
    assert cfg.vlm.model  # 非空默认
    assert cfg.vlm.format == "chat"


def test_load_config_reads_json_and_env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(
        json.dumps(
            {
                "server": {"bind_port": 9000},
                "vlm": {"model": "qwen-vl-max"},
            }
        )
    )
    monkeypatch.setenv("QWEN_MM_PROXY_BIND_PORT", "9100")  # env > file
    cfg = load_config(str(cfg_path))
    assert cfg.bind_port == 9100
    assert cfg.vlm.model == "qwen-vl-max"


def test_empty_env_vlm_api_key_clears_config_key(tmp_path: Path, monkeypatch):
    """T7：环境变量显式设为空串必须清掉 proxy.json 里的 VLM key（而非被 falsy 吞掉）。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"vlm": {"model": "qwen-vl-max", "api_key": "super-secret"}}))
    monkeypatch.setenv("QWEN_MM_PROXY_VLM_API_KEY", "")
    cfg = load_config(str(cfg_path))
    assert cfg.vlm.model == "qwen-vl-max"  # 其他字段不受影响
    assert cfg.vlm.api_key == ""  # key 被显式清空


def test_unset_env_vlm_api_key_keeps_config_key(tmp_path: Path, monkeypatch):
    """仅当环境变量没设时才保留配置里的 key（空串 vs 未设置二者语义不同）。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"vlm": {"api_key": "super-secret"}}))
    monkeypatch.delenv("QWEN_MM_PROXY_VLM_API_KEY", raising=False)
    cfg = load_config(str(cfg_path))
    assert cfg.vlm.api_key == "super-secret"


def test_relay_via_field_optional_and_validated():
    # via 可选、纯描述性；合法值（cc-switch / codex-plus）通过，非法值报 ConfigError
    r = RelayConfig(name="x", protocol="responses", base_url="http://127.0.0.1:57321/v1", via="codex-plus")
    assert r.via == "codex-plus"
    r0 = RelayConfig(name="y", protocol="chat", base_url="http://x")
    assert r0.via is None  # 缺省=一层
    with pytest.raises(ConfigError):
        RelayConfig(name="bad", protocol="chat", base_url="http://x", via="nope")


def test_missing_config_file_uses_defaults(tmp_path: Path):
    cfg = load_config(str(tmp_path / "nope.json"))
    assert cfg.bind_port == 8787


def test_malformed_bind_port_raises_config_error(tmp_path: Path):
    """坏类型（bind_port 非 number）显式报错，不再静默回退默认。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"server": {"bind_port": "not-a-number"}}))
    with pytest.raises(ConfigError):
        load_config(str(cfg_path))


def test_malformed_relays_not_list_raises_config_error(tmp_path: Path):
    """relays 非 list 显式报错，不再静默回退默认。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"relays": "not-a-list"}))
    with pytest.raises(ConfigError):
        load_config(str(cfg_path))


def test_load_config_invalid_json_raises(tmp_path: Path):
    """JSON 语法损坏 -> ConfigError（不是静默默认）。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read proxy.json"):
        load_config(str(cfg_path))


def test_load_config_top_level_not_object_raises(tmp_path: Path):
    """顶层非对象 -> ConfigError。"""
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a JSON object"):
        load_config(str(cfg_path))


def test_protocol_enum_matches_runtime_inbound_protocols():
    """锁死 config.PROTOCOLS 与 server 入站路径/解析器集合的同步不变量。
    单边新增协议（config 加了枚举但 ir/server 没加，或反之）会让请求级 relay
    回退因 __post_init__ 强校验抛 ConfigError 打成 502 fail-open。"""
    from qwen_mm_plugins_proxy.server import _PARSERS, _PROTO_BY_PATH

    assert set(PROTOCOLS) == set(_PARSERS) == set(_PROTO_BY_PATH.values())


def test_relay_protocols_enum_has_three():
    assert set(PROTOCOLS) == {"anthropic", "responses", "chat"}


VALID_RELAY = {
    "name": "deepseek",
    "protocol": "chat",
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-test",
    "models": ["deepseek-*"],
}


@pytest.mark.parametrize("protocol", ["anthropic", "responses", "chat"])
def test_relay_valid_protocol_loads(protocol):
    cfg = ProxyConfig.from_dict({"relays": [{**VALID_RELAY, "protocol": protocol}]})
    assert cfg.relays[0].protocol == protocol


def test_relay_invalid_protocol_raises_with_name():
    with pytest.raises(ConfigError, match="deepseek"):
        ProxyConfig.from_dict({"relays": [{**VALID_RELAY, "protocol": "foo"}]})


def test_relay_missing_protocol_raises_with_index_and_name():
    bad = {k: v for k, v in VALID_RELAY.items() if k != "protocol"}
    with pytest.raises(ConfigError, match=r"relays\[0\].*deepseek"):
        ProxyConfig.from_dict({"relays": [bad]})


def test_relay_unknown_field_raises():
    with pytest.raises(ConfigError, match="baseurl"):
        ProxyConfig.from_dict({"relays": [{**VALID_RELAY, "baseurl": "http://x"}]})


def test_relay_non_object_entry_raises():
    with pytest.raises(ConfigError, match=r"relays\[0\]"):
        ProxyConfig.from_dict({"relays": ["nope"]})


def test_cli_returns_2_on_config_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path))
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({"relays": [{**VALID_RELAY, "protocol": "bogus"}]}))
    rc = cli_main(["check"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "config error" in captured.err
    assert "protocol must be one of" in captured.err


def test_cli_lifecycle_commands_work_with_broken_config(tmp_path: Path, monkeypatch, capsys):
    """stop/status/logs 不依赖配置：损坏的 proxy.json 不能锁死生命周期命令。"""
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "proxy.json").write_text("{ broken", encoding="utf-8")
    rc = cli_main(["status"])
    captured = capsys.readouterr()
    assert rc == 1  # not running（无 pid 文件）
    assert "config error" not in captured.err


def test_proxy_manifests_are_standalone_non_mcp():
    """proxy ships harness manifests as a non-MCP server: empty skills, no MCP launch spec."""
    cap_dir = Path(__file__).resolve().parents[1] / "src" / "capabilities" / "proxy"
    for rel in (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        ".qoder-plugin/plugin.json",
    ):
        data = json.loads((cap_dir / rel).read_text())
        assert data["name"] == "qwen-mm-plugins-proxy"
        assert data["version"] == __version__
        assert data["skills"] == []
        assert "mcpServers" not in data
    assert json.loads((cap_dir / ".mcp.json").read_text()) == {"mcpServers": {}}
