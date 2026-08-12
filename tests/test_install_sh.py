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
    (checkout / "src/capabilities/search/.claude-plugin").mkdir(parents=True)
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
    (checkout / "src/capabilities/search/.claude-plugin/plugin.json").write_text(
        json.dumps(
            {
                "name": "qwen-mm-plugins-search",
                "version": "1.0.1",
                "skills": ["./skill"],
            }
        )
        + "\n"
    )
    shutil.copy2(ROOT / "scripts/rewrite_plugin_sources.py", checkout / "scripts")
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

    result = subprocess.run(
        [
            "python3",
            str(checkout / "scripts/rewrite_plugin_sources.py"),
            "--repo",
            str(checkout),
            "--restore",
            "core",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    restored = (checkout / "src/capabilities/core/.mcp.json").read_text()
    assert "@qwen-mm-plugins-core-v1.0.1" in restored
    assert "--refresh" not in restored


def test_rewrite_plugin_sources_all_skips_skill_only_manifests(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    skill_manifest = checkout / "src/capabilities/search/.claude-plugin/plugin.json"
    original = skill_manifest.read_text()

    result = subprocess.run(
        [
            "python3",
            str(checkout / "scripts/rewrite_plugin_sources.py"),
            "--repo",
            str(checkout),
            "--refresh",
            "all",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert skill_manifest.read_text() == original

    marketplace = json.loads((checkout / ".claude-plugin/marketplace.json").read_text())
    sources = {item["name"]: item["source"] for item in marketplace["plugins"]}
    assert sources["qwen-mm-plugins-core"] == "./src/capabilities/core"
    assert sources["qwen-mm-plugins-search"] == "./src/capabilities/search"


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


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude", "claude plugin install qwen-mm-plugins-core@qwen-mm-plugins"),
        ("codex", "codex plugin add qwen-mm-plugins-core@qwen-mm-plugins"),
        ("qoder", "qodercli plugins install qwen-mm-plugins-core@qwen-mm-plugins"),
        ("openclaw", "openclaw plugins install qwen-mm-plugins-core --marketplace"),
        ("qwen-code", "qwen extensions install"),
        ("gemini", "gemini mcp add -s user qwen-mm-plugins-core uvx --refresh --from"),
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


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        (
            "claude",
            (
                "claude plugin marketplace update qwen-mm-plugins",
                "claude plugin update qwen-mm-plugins-core@qwen-mm-plugins",
            ),
        ),
        (
            "codex",
            (
                "codex plugin marketplace upgrade qwen-mm-plugins",
                "codex plugin add qwen-mm-plugins-core@qwen-mm-plugins",
            ),
        ),
        (
            "qoder",
            (
                "qodercli plugins marketplace update qwen-mm-plugins",
                "qodercli plugins update qwen-mm-plugins-core@qwen-mm-plugins",
            ),
        ),
        ("openclaw", ("openclaw plugins update qwen-mm-plugins-core",)),
        (
            "qwen-code",
            (
                "git ls-remote --exit-code",
                "qwen extensions uninstall qwen-mm-plugins-core",
                "qwen extensions install",
                "--ref=qwen-mm-plugins-core-v1.0.1",
            ),
        ),
        (
            "gemini",
            (
                "gemini mcp add -s user qwen-mm-plugins-core uvx --from",
                "fetch --depth 1 origin qwen-mm-plugins-core-v1.0.1",
                "gemini skills install",
            ),
        ),
    ],
)
def test_update_uses_each_harness_native_refresh_path(harness, expected):
    result = _bash(
        "configured_marketplace_source() { printf remote; }; "
        f"confirm() {{ return 1; }}; update_for {harness} qwen-mm-plugins-core"
    )
    assert result.returncode == 0, result.stderr
    for command in expected:
        assert command in result.stdout
    if harness == "qwen-code":
        assert result.stdout.index("git ls-remote") < result.stdout.index("extensions uninstall")


def test_stable_update_rejects_a_local_marketplace(tmp_path):
    result = _bash(
        'configured_marketplace_source() { printf "%s" "$TEST_REPO"; }; update_for codex qwen-mm-plugins-core',
        TEST_REPO=str(tmp_path),
    )
    assert result.returncode == 1
    assert "currently uses the local marketplace" in result.stdout


def test_qwen_update_restores_previous_ref_when_new_install_fails(tmp_path):
    metadata = tmp_path / ".qwen/extensions/qwen-mm-plugins-core/.qwen-extension-install.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"ref": "qwen-mm-plugins-core-v1.0.0"}) + "\n")
    script = r"""
confirm() { return 0; }
run_cmd() {
  printf '$ %s\n' "$*"
  case "$*" in *--ref=qwen-mm-plugins-core-v1.0.1*) return 1 ;; *) return 0 ;; esac
}
update_for qwen-code qwen-mm-plugins-core
test "$?" -eq 1
"""
    result = _bash(script, HOME=str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "restoring qwen-mm-plugins-core from its previous ref qwen-mm-plugins-core-v1.0.0" in result.stdout
    assert "--ref=qwen-mm-plugins-core-v1.0.0" in result.stdout


def test_cap_spec_defaults_to_capability_stable_tag():
    result = _bash("REPO_REF=; cap_spec search")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("@qwen-mm-plugins-search-v1.0.1")


def test_explicit_ref_overrides_package_and_marketplace():
    result = _bash(
        'REPO_REF=qwen-mm-plugins-search-v1.0.1; printf "%s\\n" "$(cap_spec search)" "$(marketplace_source)"'
    )
    assert result.returncode == 0, result.stderr
    package, marketplace = result.stdout.splitlines()
    assert package.endswith("@qwen-mm-plugins-search-v1.0.1")
    assert marketplace.endswith(".git#qwen-mm-plugins-search-v1.0.1")


def test_gemini_skill_checkout_uses_same_stable_tag():
    result = _bash("QMP_DRY=1; REPO_REF=; install_gemini_skill gemini search")
    assert result.returncode == 0, result.stderr
    assert "fetch --depth 1 origin qwen-mm-plugins-search-v1.0.1" in result.stdout
    assert "--path src/capabilities/search/skill" in result.stdout


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude", "/reload-plugins"),
        ("codex", "start a new task"),
        ("qoder", "/plugins reload"),
        ("openclaw", "openclaw gateway restart"),
        ("qwen-code", "restart Qwen Code"),
        ("gemini", "/skills reload and /mcp reload"),
    ],
)
def test_post_update_hint_explains_how_to_activate_updated_components(harness, expected):
    result = _bash(f"post_update_hint {harness}")
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout


def test_manual_update_prints_same_tag_for_skill_and_mcp_without_claiming_detection():
    result = _bash("show_manual update qwen-mm-plugins-search")
    assert result.returncode == 0, result.stderr
    tag = "qwen-mm-plugins-search-v1.0.1"
    repo = "https://github.com/QwenLM/Qwen-MM-Plugins.git"
    assert f"/tree/{tag}/src/capabilities/search/skill" in result.stdout
    assert f"qwen-mm-plugins[search] @ git+{repo}@{tag}" in result.stdout
    assert "cannot safely infer their current versions" in result.stdout
    assert "do NOT receive a portable native update notification" in result.stdout


@pytest.mark.parametrize("width", [24, 36, 52])
def test_capability_rows_never_wrap_at_narrow_terminal_widths(width):
    result = _bash(f"term_cols() {{ printf {width}; }}; load_caps core; _multi_rows 0")
    assert result.returncode == 0, result.stderr
    lines = [line.replace("\x1b[2K", "") for line in result.stdout.splitlines()]
    assert len(lines) == 8
    assert all(len(line) < width for line in lines), result.stdout


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
