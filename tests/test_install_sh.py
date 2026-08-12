"""Regression checks for the installer's non-interactive helper functions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _bash(script: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NO_COLOR": "1", **env_overrides}
    return subprocess.run(
        ["bash", "-c", f"source ./install.sh --help >/dev/null; {script}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_run_cmd_propagates_command_failure():
    result = _bash('QMP_DRY=0; run_cmd false >/dev/null 2>&1; test "$?" -eq 1')
    assert result.returncode == 0, result.stderr


def test_cap_spec_uses_file_url_for_local_checkout(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    result = _bash("REPO_URL=$TEST_REPO; cap_spec core", TEST_REPO=str(checkout))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"qwen-mm-plugins[core] @ file://{str(checkout).replace(' ', '%20')}"


def _make_local_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout with spaces"
    (checkout / ".claude-plugin").mkdir(parents=True)
    (checkout / "scripts").mkdir()
    (checkout / "src/capabilities/core/.claude-plugin").mkdir(parents=True)
    (checkout / "src/capabilities/search").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    (checkout / "plugin-versions.json").write_text(
        json.dumps({"distribution": "1.0.1", "plugins": {"core": "1.0.1", "search": "1.0.1"}}) + "\n"
    )
    (checkout / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "qwen-mm-plugins-core",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://example.test/repo.git",
                            "path": "src/capabilities/core",
                            "ref": "qwen-mm-plugins-core-v1.0.1",
                        },
                    },
                    {
                        "name": "qwen-mm-plugins-search",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://example.test/repo.git",
                            "path": "src/capabilities/search",
                            "ref": "qwen-mm-plugins-search-v1.0.1",
                        },
                    },
                ]
            }
        )
        + "\n"
    )
    manifest = {
        "mcpServers": {
            "qwen-mm-plugins-core": {
                "command": "uvx",
                "args": [
                    "--from",
                    "qwen-mm-plugins[core] @ git+https://example.test/repo.git@qwen-mm-plugins-core-v1.0.1",
                    "qwen-mm-plugins-core",
                ],
            }
        }
    }
    for path in (
        checkout / "src/capabilities/core/.claude-plugin/plugin.json",
        checkout / "src/capabilities/core/.mcp.json",
    ):
        path.write_text(json.dumps(manifest) + "\n")
    shutil.copy2(ROOT / "scripts/rewrite_plugin_sources.py", checkout / "scripts")
    shutil.copy2(ROOT / "scripts/dev-plugin.sh", checkout / "scripts")
    return checkout


def test_rewrite_plugin_sources_localizes_catalog_and_mcp(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    result = subprocess.run(
        [
            "python3",
            str(checkout / "scripts/rewrite_plugin_sources.py"),
            "--repo",
            str(checkout),
            "--refresh",
            "core",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    marketplace = json.loads((checkout / ".claude-plugin/marketplace.json").read_text())
    sources = {item["name"]: item["source"] for item in marketplace["plugins"]}
    assert sources["qwen-mm-plugins-core"] == "./src/capabilities/core"
    assert isinstance(sources["qwen-mm-plugins-search"], dict)

    for path in (
        checkout / "src/capabilities/core/.claude-plugin/plugin.json",
        checkout / "src/capabilities/core/.mcp.json",
    ):
        args = next(iter(json.loads(path.read_text())["mcpServers"].values()))["args"]
        assert args[0] == "--refresh"
        assert f"qwen-mm-plugins[core] @ {checkout.as_uri()}" in args


def test_dev_plugin_uses_shared_rewriter(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    result = subprocess.run(
        ["bash", str(checkout / "scripts/dev-plugin.sh"), "core"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert checkout.as_uri() in (checkout / "src/capabilities/core/.mcp.json").read_text()

    result = subprocess.run(
        ["bash", str(checkout / "scripts/dev-plugin.sh"), "core", "--revert"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    restored = (checkout / "src/capabilities/core/.mcp.json").read_text()
    assert "@qwen-mm-plugins-core-v1.0.1" in restored
    assert "--refresh" not in restored


def test_local_checkout_root_comes_from_install_script_not_cwd(tmp_path):
    result = subprocess.run(
        ["bash", "-c", f"source {ROOT / 'install.sh'} --help >/dev/null; local_checkout_root"],
        cwd=tmp_path,
        env={**os.environ, "NO_COLOR": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(ROOT)


def test_local_install_dry_run_does_not_rewrite_manifests(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    manifest = checkout / "src/capabilities/core/.mcp.json"
    original = manifest.read_text()
    result = _bash(
        'LOCAL_REPO_ROOT="$TEST_REPO"; REPO_URL="$TEST_REPO"; '
        "confirm() { return 1; }; install_for qoder qwen-mm-plugins-core",
        TEST_REPO=str(checkout),
    )
    assert result.returncode == 0, result.stderr
    assert manifest.read_text() == original


def test_marketplace_root_parsers_preserve_local_paths():
    codex = "MARKETPLACE ROOT\\nqwen-mm-plugins /tmp/local checkout\\n"
    result = _bash(f"printf '{codex}' | marketplace_root_from_list qwen-mm-plugins")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "/tmp/local checkout\n"

    claude = "Configured marketplaces:\\n\\n  ❯ qwen-mm-plugins\\n    Source: Directory (/tmp/local checkout)\\n"
    result = _bash(f"printf '{claude}' | claude_marketplace_root_from_list qwen-mm-plugins")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "/tmp/local checkout\n"


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude", "claude plugin install qwen-mm-plugins-core@qwen-mm-plugins"),
        ("codex", "codex plugin add qwen-mm-plugins-core@qwen-mm-plugins"),
        ("qoder", "qodercli plugins install qwen-mm-plugins-core@qwen-mm-plugins"),
        ("openclaw", "openclaw plugins install qwen-mm-plugins-core --marketplace"),
        ("qwen-code", "qwen extensions install"),
        ("gemini", "gemini mcp add -s user qwen-mm-plugins-core uvx --from"),
    ],
)
def test_local_install_uses_each_harness_native_command(tmp_path, harness, expected):
    checkout = _make_local_checkout(tmp_path)
    result = _bash(
        'LOCAL_REPO_ROOT="$TEST_REPO"; REPO_URL="$TEST_REPO"; '
        f"confirm() {{ return 1; }}; install_for {harness} qwen-mm-plugins-core",
        TEST_REPO=str(checkout),
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert str(checkout) in result.stdout


def test_cap_spec_defaults_to_capability_stable_tag():
    result = _bash("REPO_REF=; cap_spec search")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("@qwen-mm-plugins-search-v1.0.1")


def test_cap_spec_honors_explicit_ref_override():
    result = _bash("REPO_REF=main; cap_spec search")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("@main")


def test_marketplace_source_honors_explicit_git_ref():
    result = _bash("REPO_REF=qwen-mm-plugins-search-v1.0.1; marketplace_source")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(".git#qwen-mm-plugins-search-v1.0.1")


def test_marketplace_source_defaults_to_unpinned_catalog():
    result = _bash("REPO_REF=; marketplace_source")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "https://github.com/QwenLM/Qwen-MM-Plugins.git"


def test_openclaw_uses_an_installer_managed_local_marketplace(tmp_path):
    checkout = tmp_path / "openclaw-marketplace"
    result = _bash(
        "QMP_DRY=1; REPO_REF=; OPENCLAW_MARKETPLACE_DIR=$TEST_CHECKOUT; prepare_openclaw_marketplace",
        TEST_CHECKOUT=str(checkout),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(checkout)


def test_gemini_skill_checkout_uses_same_stable_tag():
    result = _bash("QMP_DRY=1; REPO_REF=; install_gemini_skill gemini search")
    assert result.returncode == 0, result.stderr
    assert "fetch --depth 1 origin qwen-mm-plugins-search-v1.0.1" in result.stdout
    assert "--path src/capabilities/search/skill" in result.stdout


def test_installer_version_index_matches_release_index():
    versions = json.loads((ROOT / "plugin-versions.json").read_text())["plugins"]
    script = """
for cap in "${CAP_ITEMS[@]}"; do
  printf '%s=%s\\n' "$cap" "$(cap_version "$cap")"
done
"""
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    from_installer = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert from_installer == versions
