"""Proxy capability: config load/save + env override + defaults."""

from __future__ import annotations

import json
from pathlib import Path

from qwen_mm_plugins_proxy import __version__
from qwen_mm_plugins_proxy.config import default_config, load_config


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


def test_missing_config_file_uses_defaults(tmp_path: Path):
    cfg = load_config(str(tmp_path / "nope.json"))
    assert cfg.bind_port == 8787


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
