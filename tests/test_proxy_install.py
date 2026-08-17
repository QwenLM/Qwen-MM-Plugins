"""Regression checks for install.sh proxy harness-wiring helpers (non-interactive)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bash(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, "NO_COLOR": "1", **env}
    return subprocess.run(
        ["bash", "-c", f"source ./install.sh --help >/dev/null; {script}"],
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
    )


def test_proxy_check_conflicts_detects_foreign_base_url():
    """proxy_check_conflicts returns 1 when base_url points to a foreign upstream."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        codex_cfg = home / "config.toml"
        codex_cfg.write_text('[model_providers.deepseek-official]\nbase_url = "https://api.deepseek.com"\n')
        result = _bash(
            f"proxy_check_conflicts '{codex_cfg}'; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "rc=1", result.stdout


def test_proxy_check_conflicts_allows_local_proxy():
    """proxy_check_conflicts returns 0 when base_url is the local proxy."""
    port = os.environ.get("QWEN_MM_PROXY_BIND_PORT", "8787")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        codex_cfg = home / "config.toml"
        codex_cfg.write_text(f'[model_providers.local]\nbase_url = "http://127.0.0.1:{port}"\n')
        result = _bash(
            f"proxy_check_conflicts '{codex_cfg}'; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout


def test_proxy_check_conflicts_allows_missing_file():
    """proxy_check_conflicts returns 0 when config file does not exist."""
    result = _bash(
        "proxy_check_conflicts /nonexistent/config.toml; echo rc=$?",
    )
    assert result.returncode == 0, result.stderr
    assert "rc=0" in result.stdout


def test_proxy_rewrite_codex_creates_backup():
    """proxy_rewrite_codex backs up existing codex config before rewriting."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        codex_dir = home / ".codex"
        codex_dir.mkdir()
        codex_cfg = codex_dir / "config.toml"
        original = '[model_providers.local]\nbase_url = "http://127.0.0.1:8787"\n'
        codex_cfg.write_text(original)
        result = _bash(
            "proxy_rewrite_codex; echo rc=$?; ls *.bak 2>/dev/null || echo no_bak",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        bak = codex_cfg.parent / f"{codex_cfg.name}.qwen-mm-proxy.bak"
        assert bak.exists(), f"backup not created: {bak}"
        assert bak.read_text() == original


def test_proxy_rewrite_cc_creates_backup():
    """proxy_rewrite_cc backs up existing Claude settings.json before rewriting."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        original = '{"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}}\n'
        settings.write_text(original)
        result = _bash(
            "proxy_rewrite_cc; echo rc=$?; ls .claude/*.bak 2>/dev/null || echo no_bak",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        bak = settings.parent / f"{settings.name}.qwen-mm-proxy.bak"
        assert bak.exists(), f"backup not created: {bak}"
        assert bak.read_text() == original


def test_proxy_rewrite_codex_skips_missing_config():
    """proxy_rewrite_codex is a no-op when codex config does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = _bash(
            "proxy_rewrite_codex; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout


def test_proxy_rewrite_cc_skips_missing_config():
    """proxy_rewrite_cc is a no-op when Claude settings.json does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = _bash(
            "proxy_rewrite_cc; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout


def test_proxy_rewrite_codex_aborts_on_conflict():
    """proxy_rewrite_codex returns 1 when base_url points to foreign upstream."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        codex_dir = home / ".codex"
        codex_dir.mkdir()
        codex_cfg = codex_dir / "config.toml"
        codex_cfg.write_text('[model_providers.deepseek]\nbase_url = "https://api.deepseek.com"\n')
        result = _bash(
            "proxy_rewrite_codex; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "rc=1", result.stdout
        bak = codex_cfg.parent / f"{codex_cfg.name}.qwen-mm-proxy.bak"
        assert not bak.exists(), "should not create backup on conflict abort"


def test_proxy_restore_base_urls_restores_from_backup():
    """proxy_restore_base_urls restores original configs from .qwen-mm-proxy.bak files."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        codex_dir = home / ".codex"
        codex_dir.mkdir()
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        original_codex = '[model_providers.x]\nbase_url = "old"\n'
        original_claude = '{"env": {}}\n'
        # Write .bak files as if backup happened
        (codex_dir / "config.toml.qwen-mm-proxy.bak").write_text(original_codex)
        (claude_dir / "settings.json.qwen-mm-proxy.bak").write_text(original_claude)
        # Write "rewritten" (proxy) content
        (codex_dir / "config.toml").write_text('[model_providers.x]\nbase_url = "http://127.0.0.1:8787"\n')
        (claude_dir / "settings.json").write_text('{"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"}}\n')
        result = _bash(
            "proxy_restore_base_urls; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        # Files should be restored to original
        assert (codex_dir / "config.toml").read_text() == original_codex
        assert (claude_dir / "settings.json").read_text() == original_claude
        # .bak files should be removed
        assert not (codex_dir / "config.toml.qwen-mm-proxy.bak").exists()
        assert not (claude_dir / "settings.json.qwen-mm-proxy.bak").exists()


def test_proxy_restore_base_urls_is_noop_without_backups():
    """proxy_restore_base_urls works fine when no backup files exist."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = _bash(
            "proxy_restore_base_urls; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        assert "proxy base_url 已回滚" in result.stdout


def test_proxy_rewrite_codex_modifies_base_url():
    """proxy_rewrite_codex actually rewrites all base_url values in codex config."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        codex_dir = home / ".codex"
        codex_dir.mkdir()
        codex_cfg = codex_dir / "config.toml"
        original = (
            "[model_providers.deepseek-official]\n"
            'base_url = "http://127.0.0.1:8787"\n'
            "\n"
            "[model_providers.openai]\n"
            'base_url = "https://api.openai.com/v1"\n'
        )
        codex_cfg.write_text(original)
        result = _bash(
            "proxy_rewrite_codex; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        # Content should be modified: all base_url values point to proxy
        rewritten = codex_cfg.read_text()
        assert 'base_url = "http://127.0.0.1:8787"' in rewritten
        assert "api.openai.com" not in rewritten
        # Backup should preserve original
        bak = codex_cfg.parent / f"{codex_cfg.name}.qwen-mm-proxy.bak"
        assert bak.exists()
        assert bak.read_text() == original


def test_proxy_rewrite_cc_modifies_settings():
    """proxy_rewrite_cc actually merges ANTHROPIC_BASE_URL into settings.json."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        original = '{"env": {"OTHER_KEY": "keep"}}\n'
        settings.write_text(original)
        result = _bash(
            "proxy_rewrite_cc; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        # Content should have ANTHROPIC_BASE_URL set to proxy
        import json

        rewritten = json.loads(settings.read_text())
        assert rewritten["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8787"
        # Existing key preserved
        assert rewritten["env"]["OTHER_KEY"] == "keep"
        # Backup preserves original
        bak = settings.parent / f"{settings.name}.qwen-mm-proxy.bak"
        assert bak.exists()
        assert json.loads(bak.read_text()) == {"env": {"OTHER_KEY": "keep"}}


def test_proxy_rewrite_cc_adds_env_section():
    """proxy_rewrite_cc creates env section if missing."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text("{}\n")
        result = _bash(
            "proxy_rewrite_cc; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        import json

        rewritten = json.loads(settings.read_text())
        assert rewritten["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8787"


def test_proxy_rewrite_qwen_code_creates_env():
    """proxy_rewrite_qwen_code creates .env file with DASHSCOPE_BASE_URL."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = _bash(
            "proxy_rewrite_qwen_code; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        env_file = home / ".qwen-code" / ".env"
        assert env_file.exists(), f"env file not created: {env_file}"
        assert "DASHSCOPE_BASE_URL=http://127.0.0.1:8787" in env_file.read_text()


def test_proxy_rewrite_qwen_code_updates_existing():
    """proxy_rewrite_qwen_code updates DASHSCOPE_BASE_URL in existing .env."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        qwen_dir = home / ".qwen-code"
        qwen_dir.mkdir()
        env_file = qwen_dir / ".env"
        original = "DASHSCOPE_API_KEY=old\nDASHSCOPE_BASE_URL=https://old.example.com\n"
        env_file.write_text(original)
        result = _bash(
            "proxy_rewrite_qwen_code; echo rc=$?",
            HOME=str(home),
        )
        assert result.returncode == 0, result.stderr
        rewritten = env_file.read_text()
        assert "DASHSCOPE_BASE_URL=http://127.0.0.1:8787" in rewritten
        assert "DASHSCOPE_API_KEY=old" in rewritten  # other keys preserved
        assert "old.example.com" not in rewritten
        # Backup preserves original
        bak = env_file.parent / f"{env_file.name}.qwen-mm-proxy.bak"
        assert bak.exists()
        assert bak.read_text() == original
