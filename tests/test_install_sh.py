"""Regression checks for the installer's non-interactive helper functions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from shared.env import CONFIG_FIELDS, _parse_config

ROOT = Path(__file__).resolve().parents[1]


def _release_tag(cap: str) -> str:
    index = json.loads((ROOT / "plugin-versions.json").read_text(encoding="utf-8"))
    return index["tag_format"].format(cap=cap, version=index["plugins"][cap])


@pytest.fixture
def installer_env(tmp_path):
    """Isolate configuration and replace native CLIs with offline stand-ins."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    state = tmp_path / "installed.txt"
    state.write_text("core\nsearch\n")
    for harness in ("qwen", "gemini"):
        for cap in ("core", "search"):
            (tmp_path / f"home/.{harness}/extensions/qwen-mm-plugins-{cap}").mkdir(parents=True)
    for binary in ("claude", "codebuddy", "codex", "qodercli", "openclaw", "qwen", "gemini", "uvx", "git"):
        path = fake_bin / binary
        path.write_text(
            "#!/bin/bash\n"
            'printf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            "if read -r unexpected; then exit 92; fi\n"
            'if [ "${0##*/} $*" = "${FAIL_COMMAND:-}" ]; then exit 23; fi\n'
            'case "$*" in\n'
            '  "plugin list"|"plugins list")\n'
            '    while read -r cap; do printf "qwen-mm-plugins-%s@qwen-mm-plugins\\n" "$cap"; done < "$PLUGIN_STATE" ;;\n'
            '  "plugin uninstall "*|"plugin remove "*|"plugins uninstall "*|"extensions uninstall "*)\n'
            "    cap=${3#qwen-mm-plugins-}; cap=${cap%@*}\n"
            '    grep -vxF "$cap" "$PLUGIN_STATE" > "$PLUGIN_STATE.tmp" || true\n'
            '    mv "$PLUGIN_STATE.tmp" "$PLUGIN_STATE" ;;\n'
            "esac\n"
            "exit 0\n"
        )
        path.chmod(0o755)

    return {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "NO_COLOR": "1",
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "PLUGIN_STATE": str(state),
        "QMP_REPO": "https://example.test/repo.git",
        "QMP_REF": "",
        "QWEN_MM_CONFIG_DIR": str(tmp_path / "config"),
        "QWEN_MM_CONFIG": str(tmp_path / "config/settings"),
    }


@pytest.fixture
def installer_cli(installer_env, tmp_path):
    """Run the real CLI without a controlling TTY."""

    def run(*args, script=ROOT / "install.sh", **overrides):
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=tmp_path,
            env={**installer_env, **overrides},
            # Give the installer input that must never reach the native harness subprocesses.
            input="not an answer\n",
            capture_output=True,
            text=True,
            start_new_session=True,
            timeout=15,
        )

    return run


@pytest.mark.parametrize("harness", ["claude", "codebuddy", "codex", "qoder", "openclaw", "qwen-code", "gemini"])
def test_headless_local_install_executes_without_prompts(installer_cli, tmp_path, harness):
    checkout = _make_local_checkout(tmp_path)
    shutil.copy2(ROOT / "install.sh", checkout)
    result = installer_cli("local", "--plugin", "core", "--harness", harness, script=checkout / "install.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text()
    assert str(checkout) in commands
    assert "uvx --from qwen-mm-plugins[core] @ file://" in commands
    assert "--check-system" in commands
    assert "[Y/n]" not in result.stdout
    assert "configure it now" not in result.stdout
    assert not (tmp_path / "config").exists()
    if harness != "gemini":
        manifest = (checkout / "src/capabilities/core/.mcp.json").read_text()
        assert checkout.as_uri() in manifest
        assert "--refresh" in manifest


def test_headless_release_install_accepts_and_deduplicates_multiple_plugins(installer_cli, tmp_path):
    result = installer_cli(
        "install", "--harness", "codex", "--plugin", "core,search", "--plugin", "qwen-mm-plugins-core"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text()
    assert commands.count("codex plugin add qwen-mm-plugins-core@") == 1
    assert commands.count("codex plugin add qwen-mm-plugins-search@") == 1
    assert f"@{_release_tag('core')}" in commands
    assert f"@{_release_tag('search')}" in commands


def test_headless_local_dry_run_keeps_manifests_unchanged(installer_cli, tmp_path):
    checkout = _make_local_checkout(tmp_path)
    shutil.copy2(ROOT / "install.sh", checkout)
    before = {path: path.read_bytes() for path in checkout.rglob("*.json")}
    result = installer_cli(
        "local", "--plugin", "all", "--harness", "codex", "--dry-run", script=checkout / "install.sh"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "codex plugin add qwen-mm-plugins-core@" in result.stdout
    assert "codex plugin add qwen-mm-plugins-video-spatio@" in result.stdout
    assert before == {path: path.read_bytes() for path in before}
    assert (tmp_path / "commands.log").read_text().splitlines() == ["codex plugin marketplace list"]
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("local", "--plugin", "core"),
        ("install", "--harness", "codex"),
        ("local", "--plugin"),
        ("local", "--plugin", "--harness", "codex"),
        ("local", "--plugin", "core", "--harness", "unknown"),
        ("local", "--plugin", "core", "--harness", "claude codebuddy"),
        ("local", "--plugin", "unknown", "--harness", "codex"),
        ("local", "--plugin", "core api", "--harness", "codex"),
        ("local", "--plugin", "core\nunknown", "--harness", "codex"),
        ("local", "--plugin", "core,", "--harness", "codex"),
        ("local", "--plugin", "core", "--harness", "codex", "--harness", "claude"),
        ("local", "--restore", "--plugin", "core", "--harness", "codex"),
        ("update", "--harness", "codex"),
        ("uninstall", "--plugin", "core"),
        ("verify", "--dry-run"),
        ("config-set", "QWEN_MM_NATIVE_MODE=1"),
        ("install", "--dry-run"),
        ("install", "--typo"),
        ("instal",),
    ],
)
def test_headless_invalid_arguments_fail_before_side_effects(installer_cli, tmp_path, args):
    result = installer_cli(*args)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Try install.sh --help" in result.stderr
    assert not (tmp_path / "commands.log").exists()
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize(
    "command",
    [
        "codex plugin add qwen-mm-plugins-core@qwen-mm-plugins",
        f"uvx --from qwen-mm-plugins[core] @ git+https://example.test/repo.git@{_release_tag('core')} "
        "qwen-mm-plugins-core --check-system",
    ],
)
def test_headless_install_propagates_harness_and_system_check_failures(installer_cli, command):
    result = installer_cli("install", "--plugin", "core", "--harness", "codex", FAIL_COMMAND=command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[Y/n]" not in result.stdout


@pytest.mark.parametrize("missing", ["codex", "uvx"])
def test_headless_missing_prerequisite_fails_without_installing(missing):
    result = _bash(
        "CLI_HARNESS=codex; CLI_CAPS=core; "
        f'have() {{ [ "$1" != {missing} ]; }}; '
        'confirm() { printf "UNEXPECTED prompt\\n"; return 0; }; '
        'install_for() { printf "UNEXPECTED installation\\n"; }; do_install'
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "UNEXPECTED" not in result.stdout


def test_headless_skill_only_install_does_not_require_uv():
    result = _bash(
        "CLI_HARNESS=codex; CLI_CAPS=edu-agent; "
        'have() { [ "$1" != uvx ]; }; '
        'install_for() { printf "installed %s\\n" "$*"; }; do_install'
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "installed codex qwen-mm-plugins-edu-agent" in result.stdout


@pytest.mark.parametrize("action", ["update", "uninstall"])
@pytest.mark.parametrize("harness", ["claude", "codebuddy", "codex", "qoder", "openclaw", "qwen-code", "gemini"])
def test_headless_update_and_uninstall_use_native_commands(installer_cli, tmp_path, action, harness):
    result = installer_cli(action, "--plugin", "core", "--harness", harness)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text()
    assert "[Y/n]" not in result.stdout
    assert "[y/N]" not in result.stdout
    assert "qwen-mm-plugins-core" in commands
    if action == "update":
        assert "--check-system" in commands
        assert f"@{_release_tag('core')}" in commands
    else:
        assert "uvx" not in commands
        assert "marketplace remove" not in commands  # search is still installed


@pytest.mark.parametrize("action,verb", [("update", "add"), ("uninstall", "remove")])
def test_headless_all_only_targets_installed_plugins(installer_cli, tmp_path, action, verb):
    result = installer_cli(action, "--plugin", "all", "--harness", "codex")
    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text().splitlines()
    assert [line for line in commands if line.startswith(f"codex plugin {verb} ")] == [
        f"codex plugin {verb} qwen-mm-plugins-core@qwen-mm-plugins",
        f"codex plugin {verb} qwen-mm-plugins-search@qwen-mm-plugins",
    ]


@pytest.mark.parametrize("action", ["update", "uninstall", "verify"])
def test_headless_missing_plugin_fails_before_mutation(installer_cli, tmp_path, action):
    result = installer_cli(action, "--plugin", "core,api", "--harness", "codex")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "api is not installed" in result.stdout
    assert (tmp_path / "commands.log").read_text().splitlines() == ["codex plugin list"]


@pytest.mark.parametrize("action", ["update", "uninstall", "verify"])
def test_headless_empty_inventory_does_not_act_on_all_plugins(installer_cli, tmp_path, action):
    (tmp_path / "installed.txt").write_text("")
    result = installer_cli(action, "--plugin", "all", "--harness", "codex")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no matching plugins" in result.stdout
    assert (tmp_path / "commands.log").read_text().splitlines() == ["codex plugin list"]


@pytest.mark.parametrize("action", ["update", "uninstall", "verify"])
def test_headless_dry_run_preserves_config_and_skips_native_mutations(installer_cli, tmp_path, action):
    config = tmp_path / "config/settings"
    config.parent.mkdir()
    config.write_text("DASHSCOPE_API_KEY=keep\n")
    result = installer_cli(action, "--plugin", "core", "--harness", "codex", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text().splitlines()
    assert commands == ["codex plugin list"] + (["codex plugin marketplace list --json"] if action == "update" else [])
    assert config.read_text() == "DASHSCOPE_API_KEY=keep\n"
    assert (tmp_path / "installed.txt").read_text() == "core\nsearch\n"
    assert "[Y/n]" not in result.stdout
    if action == "verify":
        assert "planned" in result.stdout
        assert "passed" not in result.stdout


@pytest.mark.parametrize("harness", ["claude", "codebuddy"])
def test_headless_uninstall_all_removes_empty_marketplace_but_keeps_shared_config(installer_cli, tmp_path, harness):
    config = tmp_path / "config/settings"
    config.parent.mkdir()
    config.write_text("DASHSCOPE_API_KEY=keep\n")
    result = installer_cli("uninstall", "--plugin", "all", "--harness", harness)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{harness} plugin marketplace remove qwen-mm-plugins" in (tmp_path / "commands.log").read_text()
    assert config.read_text() == "DASHSCOPE_API_KEY=keep\n"
    assert "[y/N]" not in result.stdout


def test_interactive_uninstall_dry_run_cannot_delete_shared_config(installer_env, tmp_path):
    config = tmp_path / "config/settings"
    config.parent.mkdir()
    config.write_text("DASHSCOPE_API_KEY=keep\n")
    result = _bash(
        "screen() { :; }; pause() { :; }; "
        "menu_pick() { PICK_I=0; PICK=claude; }; "
        'spin() { printf -v "$2" 1; }; '
        "multi_pick() { MP_STATUS=ok; MP_SEL[0]=1; }; "
        'confirm() { case "$1" in "Run the uninstall"*) return 1 ;; *) return 0 ;; esac; }; '
        "do_uninstall",
        **installer_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_text() == "DASHSCOPE_API_KEY=keep\n"
    assert not (tmp_path / "commands.log").exists()


@pytest.mark.parametrize("action,verb", [("update", "add"), ("uninstall", "remove")])
def test_headless_update_and_uninstall_propagate_command_failure(installer_cli, action, verb):
    result = installer_cli(
        action,
        "--plugin",
        "core",
        "--harness",
        "codex",
        FAIL_COMMAND=f"codex plugin {verb} qwen-mm-plugins-core@qwen-mm-plugins",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{action} incomplete" in result.stdout


def test_headless_update_propagates_system_check_failure(installer_cli):
    result = installer_cli(
        "update",
        "--plugin",
        "core",
        "--harness",
        "codex",
        FAIL_COMMAND=f"uvx --from qwen-mm-plugins[core] @ git+https://example.test/repo.git@{_release_tag('core')} "
        "qwen-mm-plugins-core --check-system",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "failed to start" in result.stdout


@pytest.mark.parametrize("args", [("--plugin", "core,edu-agent"), ("--harness", "codex")])
def test_headless_verify_checks_requested_or_installed_plugins(installer_cli, tmp_path, args):
    result = installer_cli("verify", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text()
    assert "qwen-mm-plugins-core --check-system" in commands
    assert "qwen-mm-plugins-edu-agent --check-system" not in commands
    assert ("qwen-mm-plugins-search --check-system" in commands) == ("--harness" in args)
    assert "Verify summary" in result.stdout


def test_headless_verify_propagates_failure(installer_cli):
    result = installer_cli(
        "verify",
        "--plugin",
        "core",
        FAIL_COMMAND=f"uvx --from qwen-mm-plugins[core] @ git+https://example.test/repo.git@{_release_tag('core')} "
        "qwen-mm-plugins-core --check-system",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "failed       1" in result.stdout


def test_headless_verify_missing_uv_never_prompts():
    result = _bash('CLI_CAPS=core; have() { return 1; }; confirm() { printf "UNEXPECTED prompt\\n"; }; do_verify')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "UNEXPECTED" not in result.stdout


def test_headless_verify_skill_only_does_not_require_uv():
    result = _bash("CLI_CAPS=edu-agent; have() { return 1; }; do_verify")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped      1" in result.stdout


def test_legacy_verify_still_works(installer_cli, tmp_path):
    result = installer_cli("--verify", "core,search")
    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text()
    assert "qwen-mm-plugins-core --check-system" in commands
    assert "qwen-mm-plugins-search --check-system" in commands


def test_configure_preserves_literal_values_and_hides_secrets(installer_cli, tmp_path):
    path = tmp_path / "custom directory/nested/settings"
    secret = 'key with spaces=a=b # literal $(touch NEVER) `id` "quotes"'
    values = {"DASHSCOPE_API_KEY": secret, "QWEN_MM_CACHE": " /tmp/cache with spaces ", "QWEN_MM_NATIVE_MODE": "0"}
    result = installer_cli("configure", *(f"{key}={value}" for key, value in values.items()), QWEN_MM_CONFIG=str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _parse_config(path.read_text()) == values
    assert path.stat().st_mode & 0o777 == 0o600
    assert secret not in result.stdout + result.stderr
    assert not (tmp_path / "NEVER").exists()
    assert not (tmp_path / "commands.log").exists()


def test_configure_updates_and_clears_without_losing_other_settings(installer_cli, tmp_path):
    path = tmp_path / "config/settings"
    path.parent.mkdir()
    path.write_text(
        '# keep this comment\nexport QWEN_MM_NATIVE_MODE = "1"\nQWEN_MM_NATIVE_MODE=1\nSERPER_API_KEY=previous-secret\n'
    )
    result = installer_cli("configure", "QWEN_MM_NATIVE_MODE=0", "SERPER_API_KEY=")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _parse_config(path.read_text()) == {"QWEN_MM_NATIVE_MODE": "0"}
    assert path.read_text().count("QWEN_MM_NATIVE_MODE") == 1
    assert "# keep this comment" in path.read_text()
    assert "previous-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "bad", ["UNKNOWN=secret", "DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY=a\nb", "DASHSCOPE_API_KEY=a\rb"]
)
def test_configure_rejects_entire_invalid_batch_without_echoing_values(installer_cli, tmp_path, bad):
    path = tmp_path / "config/settings"
    path.parent.mkdir()
    original = "QWEN_MM_NATIVE_MODE=1\n"
    path.write_text(original)
    result = installer_cli("configure", "QWEN_MM_NATIVE_MODE=0", bad)
    assert result.returncode == 2, result.stdout + result.stderr
    assert path.read_text() == original
    assert bad not in result.stdout + result.stderr


def test_configure_without_assignments_remains_interactive(installer_cli, tmp_path):
    result = installer_cli("configure")
    assert result.returncode == 1
    assert "installer is interactive" in result.stderr
    assert not (tmp_path / "config").exists()


def test_configure_write_failure_is_reported(installer_cli, tmp_path):
    path = tmp_path / "not-a-directory"
    path.write_text("keep")
    result = installer_cli("configure", "DASHSCOPE_API_KEY=secret", QWEN_MM_CONFIG=str(path / "config"))
    assert result.returncode == 1
    assert "secret" not in result.stdout + result.stderr
    assert path.read_text() == "keep"


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


def test_config_spec_lists_search_backend_selector_and_keys():
    result = _bash('printf "%s\\n" "${CONFIG_SPEC[@]}"')
    assert result.returncode == 0, result.stderr
    rows = result.stdout.splitlines()
    assert any(row.startswith("QWEN_MM_SEARCH_BACKEND|0|search|auto|") for row in rows)
    assert any(row.startswith("SERPER_API_KEY|1|search||") for row in rows)
    assert any(row.startswith("TAVILY_API_KEY|1|search||") for row in rows)
    assert any(row.startswith("EXA_API_KEY|1|search||") for row in rows)
    assert any(row.startswith("SERPLY_API_KEY|1|search||") for row in rows)


def test_config_spec_lists_api_model_defaults():
    result = _bash('printf "%s\\n" "${CONFIG_SPEC[@]}"')
    assert result.returncode == 0, result.stderr
    rows = result.stdout.splitlines()
    assert any(row.startswith("MINIMAX_API_KEY|1|services||") for row in rows)
    assert any(row.startswith("QWEN_MM_API_VL_MODEL|0|services|qwen3.7-plus|") for row in rows)
    assert any(row.startswith("QWEN_MM_API_OMNI_MODEL|0|services|qwen3.8-omni-flash|") for row in rows)
    assert any(row.startswith("QWEN_MM_NATIVE_MODE|0|runtime|1|") for row in rows)


def test_config_spec_lists_omni_chatcut_model_config():
    result = _bash('printf "%s\\n" "${CONFIG_SPEC[@]}"')
    assert result.returncode == 0, result.stderr
    assert any(row.startswith("QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG|0|chatcut||") for row in result.stdout.splitlines())


def test_config_spec_mirrors_shared_catalog():
    result = _bash('printf "%s\\n" "${CONFIG_SPEC[@]}"')
    assert result.returncode == 0, result.stderr
    actual = [row.split("|", 4) for row in result.stdout.splitlines()]
    group_tags = {
        "Media APIs & endpoints": "services",
        "Omni ChatCut": "chatcut",
        "Search providers": "search",
        "Runtime paths & limits": "runtime",
        "Video-memory": "memory",
        "Omni-memory": "omni",
        "OSS storage (serve large media by URL)": "oss",
        "Blender / FreeCAD hosts": "hosts",
        "edu-agent (Node / headless Chromium)": "edu",
    }
    expected = [
        [key, str(int(secret)), group_tags[group], default, description]
        for key, secret, group, default, description in CONFIG_FIELDS
    ]
    assert actual == expected


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


def test_local_restore_cli_restores_all_published_refs(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    shutil.copy2(ROOT / "install.sh", checkout)
    localized = subprocess.run(
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
    assert localized.returncode == 0, localized.stderr

    restored = subprocess.run(
        ["bash", "install.sh", "local", "--restore"],
        cwd=checkout,
        env={**os.environ, "HOME": str(tmp_path / "home"), "NO_COLOR": "1"},
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    assert "restored published plugin refs" in restored.stdout

    marketplace = json.loads((checkout / ".claude-plugin/marketplace.json").read_text())
    sources = {item["name"]: item["source"] for item in marketplace["plugins"]}
    assert sources["qwen-mm-plugins-core"]["ref"] == "qwen-mm-plugins-core-v1.0.1"
    assert sources["qwen-mm-plugins-search"]["ref"] == "qwen-mm-plugins-search-v1.0.1"
    manifest = (checkout / "src/capabilities/core/.mcp.json").read_text()
    assert "@qwen-mm-plugins-core-v1.0.1" in manifest
    assert "--refresh" not in manifest


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


@pytest.mark.parametrize("action", ["do_install", "do_update", "do_uninstall"])
def test_missing_harness_stops_before_plugin_selection(action):
    script = rf"""
screen() {{ :; }}
hr() {{ :; }}
pick_count=0
menu_pick() {{
  pick_count=$((pick_count + 1))
  if [ "$pick_count" -eq 1 ]; then PICK_I=0; PICK=openclaw
  else PICK_I=-1; PICK=''
  fi
}}
have() {{ return 1; }}
choose_caps() {{ printf 'UNEXPECTED capability selection\n'; }}
choose_caps_local() {{ printf 'UNEXPECTED capability selection\n'; }}
choose_caps_update() {{ printf 'UNEXPECTED capability selection\n'; }}
spin() {{ printf 'UNEXPECTED installed-plugin detection\n'; }}
pause() {{ printf 'PAUSED\n'; }}
{action}
"""
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    assert "openclaw is not available — 'openclaw' is not installed or not on PATH" in result.stdout
    assert result.stdout.count("PAUSED") == 1
    assert "UNEXPECTED" not in result.stdout


def test_verify_separates_capabilities_and_summarizes_results():
    script = r"""
screen() { :; }
hr() { :; }
spin() { printf -v "$2" 'core search edu-agent'; }
load_caps() { MP_ITEMS=(core search edu-agent); MP_SEL=(1 1 1); }
multi_pick() { MP_STATUS=ok; }
ensure_uv() { return 0; }
uvx_cap() { printf 'checked %s\n' "$1"; [ "$1" != search ]; }
pause() { :; }
do_verify
"""
    result = _bash(script)
    assert result.returncode == 1
    for cap in ("core", "search", "edu-agent"):
        assert f"── qwen-mm-plugins-{cap} " in result.stdout
    assert "checked core" in result.stdout
    assert "checked search" in result.stdout
    assert "Verify summary" in result.stdout
    assert "passed       1" in result.stdout
    assert "failed       1" in result.stdout
    assert "skipped      1 (skill-only)" in result.stdout


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude", "claude plugin install qwen-mm-plugins-core@qwen-mm-plugins"),
        ("codebuddy", "codebuddy plugin install qwen-mm-plugins-core@qwen-mm-plugins"),
        ("codex", "codex plugin add qwen-mm-plugins-core@qwen-mm-plugins"),
        ("qoder", "qodercli plugins install qwen-mm-plugins-core@qwen-mm-plugins"),
        ("openclaw", "openclaw plugins install qwen-mm-plugins-core --marketplace"),
        ("qwen-code", "qwen extensions install"),
        ("gemini", "gemini mcp add -s user qwen-mm-plugins-core uvx --refresh --from"),
    ],
)
def test_local_install_uses_each_harness_native_command(installer_env, tmp_path, harness, expected):
    checkout = _make_local_checkout(tmp_path)
    result = _bash(
        'LOCAL_REPO_ROOT="$TEST_REPO"; REPO_URL="$TEST_REPO"; '
        f"confirm() {{ return 1; }}; install_for {harness} qwen-mm-plugins-core",
        TEST_REPO=str(checkout),
        **installer_env,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert str(checkout) in result.stdout


def test_harness_catalog_includes_codebuddy_but_not_ui_only_desktop_apps():
    result = _bash('printf "%s\n%s\n" "$MP_HARNESSES" "$CFG_HARNESSES"')
    assert result.returncode == 0, result.stderr
    marketplace, config = result.stdout.splitlines()
    assert "codebuddy" in marketplace.split()
    for desktop_app in ("workbuddy", "qoderwork", "qwenwork"):
        assert desktop_app not in marketplace.split()
        assert desktop_app not in config.split()


def test_codebuddy_rejects_broken_marketplace_ref_urls():
    result = _bash("REPO_REF=qwen-mm-plugins-core-v1.0.1; install_for codebuddy qwen-mm-plugins-core")
    assert result.returncode == 1
    assert "tested codebuddy client cannot install Git marketplace #ref URLs reliably" in result.stdout


def test_codebuddy_marketplace_parser_distinguishes_local_and_remote():
    local_listing = """[
  {
    "name": "qwen-mm-plugins",
    "type": "directory",
    "description": "Marketplace from /tmp/checkout with spaces"
  }
]"""
    remote_listing = """[
  {
    "name": "qwen-mm-plugins",
    "type": "git",
    "description": "Marketplace from https://example.test/repo.git"
  }
]"""
    result = _bash(
        'printf "%s\n" "$LOCAL_LISTING" | codebuddy_marketplace_root_from_list qwen-mm-plugins; '
        'printf "%s\n" "$REMOTE_LISTING" | codebuddy_marketplace_root_from_list qwen-mm-plugins',
        LOCAL_LISTING=local_listing,
        REMOTE_LISTING=remote_listing,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["/tmp/checkout with spaces", "remote"]


def test_codebuddy_install_checks_inventory_when_cli_falsely_returns_zero(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    cli = fake_bin / "codebuddy"
    cli.write_text("#!/usr/bin/env bash\nexit 0\n")
    cli.chmod(0o755)
    result = _bash(
        "confirm() { return 0; }; install_for codebuddy qwen-mm-plugins-core",
        PATH=f"{fake_bin}:{os.environ['PATH']}",
    )
    assert result.returncode == 1
    assert "CodeBuddy did not install qwen-mm-plugins-core@qwen-mm-plugins" in result.stdout


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
            "codebuddy",
            (
                "codebuddy plugin marketplace update qwen-mm-plugins",
                "codebuddy plugin update qwen-mm-plugins-core@qwen-mm-plugins",
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
                f"--ref={_release_tag('core')}",
            ),
        ),
        (
            "gemini",
            (
                "gemini mcp add -s user qwen-mm-plugins-core uvx --from",
                f"fetch --depth 1 origin {_release_tag('core')}",
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
  case "$*" in *--ref="$EXPECTED_CORE_TAG"*) return 1 ;; *) return 0 ;; esac
}
update_for qwen-code qwen-mm-plugins-core
test "$?" -eq 1
"""
    result = _bash(script, HOME=str(tmp_path), EXPECTED_CORE_TAG=_release_tag("core"))
    assert result.returncode == 0, result.stderr
    assert "restoring qwen-mm-plugins-core from its previous ref qwen-mm-plugins-core-v1.0.0" in result.stdout
    assert "--ref=qwen-mm-plugins-core-v1.0.0" in result.stdout


def test_cap_spec_defaults_to_capability_stable_tag():
    result = _bash("REPO_REF=; cap_spec search")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(f"@{_release_tag('search')}")


def test_explicit_ref_overrides_package_and_marketplace():
    result = _bash(
        'REPO_REF=qwen-mm-plugins-search-v1.0.1; printf "%s\\n" "$(cap_spec search)" "$(marketplace_source)"'
    )
    assert result.returncode == 0, result.stderr
    package, marketplace = result.stdout.splitlines()
    assert package.endswith("@qwen-mm-plugins-search-v1.0.1")
    assert marketplace.endswith(".git#qwen-mm-plugins-search-v1.0.1")


def test_gemini_skill_checkout_uses_same_stable_tag():
    result = _bash("QMP_DRY=1; REPO_REF=; install_gemini_skills gemini search")
    assert result.returncode == 0, result.stderr
    assert f"fetch --depth 1 origin {_release_tag('search')}" in result.stdout
    assert "--path src/capabilities/search/skill" in result.stdout


def test_gemini_installs_every_omni_chatcut_skill_from_its_own_root():
    result = _bash("QMP_DRY=1; REPO_REF=; install_gemini_skills gemini omni-chatcut")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("gemini skills install") == 3
    for child in ("music-to-mv", "movie-commentary", "video-translation"):
        assert f"--path src/capabilities/omni-chatcut/skill/{child}" in result.stdout
    assert "--path src/capabilities/omni-chatcut/skill --consent" not in result.stdout


def test_gemini_omni_chatcut_uninstall_names_match_skill_frontmatter():
    result = _bash(
        "for component in $(gemini_skill_components omni-chatcut); do "
        'gemini_skill_name omni-chatcut "$component"; printf "\\n"; done'
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "qwen-mm-plugins-omni-chatcut-music-to-mv",
        "qwen-mm-plugins-omni-chatcut-movie-commentary",
        "qwen-mm-plugins-omni-chatcut-video-translation",
    ]
    for name in result.stdout.splitlines():
        child = name.removeprefix("qwen-mm-plugins-omni-chatcut-")
        skill = ROOT / "src/capabilities/omni-chatcut/skill" / child / "SKILL.md"
        assert skill.is_file()
        assert f"name: {name}\n" in skill.read_text()

    uninstall = _bash("QMP_DRY=1; uninstall_gemini_skills gemini omni-chatcut")
    assert uninstall.returncode == 0, uninstall.stderr
    assert uninstall.stdout.count("gemini skills uninstall") == 3
    for name in result.stdout.splitlines():
        assert f"gemini skills uninstall {name}" in uninstall.stdout


def test_gemini_detects_omni_chatcut_from_any_installed_child_skill(tmp_path):
    skill = tmp_path / ".gemini/skills/qwen-mm-plugins-omni-chatcut-movie-commentary"
    skill.mkdir(parents=True)
    result = _bash("gemini_has_capability_skill omni-chatcut", HOME=str(tmp_path))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude", "/reload-plugins"),
        ("codebuddy", "/reload-plugins"),
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
    tag = _release_tag("search")
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
    # One row per published capability — read the count rather than restating it, so adding a
    # capability does not need this literal updated.
    published_count = len(json.loads((ROOT / "plugin-versions.json").read_text())["plugins"])
    assert len(lines) == published_count
    assert all(len(line) < width for line in lines), result.stdout
    if width >= 36:
        assert any("omni-video2note" in line for line in lines), result.stdout


def test_numeric_toggle_reaches_double_digit_rows():
    # The catalog passed ten capabilities, so the text-mode prompt has to accept "10", not just 1-9.
    script = """
load_caps ''
exec 3< <(printf '10\\n\\n')
multi_pick 'pick' >/dev/null
printf 'last=%s first=%s\\n' "${MP_SEL[9]}" "${MP_SEL[0]}"
"""
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    assert "last=1 first=0" in result.stdout, result.stdout + result.stderr


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
