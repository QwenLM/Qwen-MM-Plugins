from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

SCRIPT = Path(REPO_ROOT) / "scripts" / "tag_plugin_release.py"


def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check:
        result.check_returncode()
    return result


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(cwd, "git", *args, check=check)


def _write_release_metadata(repo: Path, version: str) -> None:
    cap = "search"
    tag = f"qwen-mm-plugins-{cap}-v{version}"
    cap_dir = repo / "src/capabilities/search"
    for directory in (
        repo / ".claude-plugin",
        cap_dir / ".claude-plugin",
        cap_dir / ".codex-plugin",
        cap_dir / ".qoder-plugin",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    (repo / "plugin-versions.json").write_text(
        json.dumps(
            {
                "distribution_version": version,
                "tag_format": "qwen-mm-plugins-{cap}-v{version}",
                "plugins": {cap: version},
            }
        )
        + "\n"
    )
    (repo / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "qwen-mm-plugins-search",
                        "source": {
                            "source": "git-subdir",
                            "path": "src/capabilities/search",
                            "ref": tag,
                        },
                    }
                ]
            }
        )
        + "\n"
    )
    args = [
        "--from",
        f"qwen-mm-plugins[search] @ git+https://example.test/repo.git@{tag}",
        "qwen-mm-plugins-search",
    ]
    (cap_dir / ".claude-plugin/plugin.json").write_text(
        json.dumps(
            {
                "name": "qwen-mm-plugins-search",
                "version": version,
                "mcpServers": {"qwen-mm-plugins-search": {"command": "uvx", "args": args}},
            }
        )
        + "\n"
    )
    for rel in (".codex-plugin/plugin.json", ".qoder-plugin/plugin.json"):
        (cap_dir / rel).write_text(json.dumps({"name": "qwen-mm-plugins-search", "version": version}) + "\n")
    (cap_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"qwen-mm-plugins-search": {"command": "uvx", "args": args}}}) + "\n"
    )


def _release_repo(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "--initial-branch=main", str(repo))
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.test")
    _git(repo, "remote", "add", "origin", str(remote))

    _write_release_metadata(repo, "1.0.1")
    (repo / "src/shared").mkdir(parents=True)
    (repo / "src/shared/runtime.py").write_text("VALUE = 1\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'fixture'\nversion = '1.0.1'\n")
    (repo / "src/capabilities/search/tools.py").write_text("BACKENDS = ['serper']\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(search): initial release")
    _git(repo, "tag", "-a", "qwen-mm-plugins-search-v1.0.1", "-m", "search 1.0.1")
    _git(repo, "push", "-u", "origin", "main", "--tags")

    (repo / "src/shared/runtime.py").write_text("VALUE = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix(shared): adjust retry behavior")
    shared_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _write_release_metadata(repo, "1.0.2")
    (repo / "src/capabilities/search/tools.py").write_text("BACKENDS = ['serper', 'exa']\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(search): support multiple backends")
    _git(repo, "push", "origin", "main")
    return repo, shared_sha


def _add_skill_only_release(repo: Path) -> None:
    cap = "edu-agent"
    version = "1.0.0"
    tag = f"qwen-mm-plugins-{cap}-v{version}"
    index_path = repo / "plugin-versions.json"
    index = json.loads(index_path.read_text())
    index["plugins"][cap] = version
    index_path.write_text(json.dumps(index) + "\n")

    marketplace_path = repo / ".claude-plugin/marketplace.json"
    marketplace = json.loads(marketplace_path.read_text())
    marketplace["plugins"].append(
        {
            "name": f"qwen-mm-plugins-{cap}",
            "source": {
                "source": "git-subdir",
                "path": f"src/capabilities/{cap}",
                "ref": tag,
            },
        }
    )
    marketplace_path.write_text(json.dumps(marketplace) + "\n")

    cap_dir = repo / "src/capabilities" / cap
    for rel in (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        ".qoder-plugin/plugin.json",
    ):
        path = cap_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"name": f"qwen-mm-plugins-{cap}", "version": version, "skills": ["./skill"]}) + "\n"
        )
    (cap_dir / "skill").mkdir()
    (cap_dir / "skill/SKILL.md").write_text("# Edu Agent\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(edu-agent): initial release")
    _git(repo, "push", "origin", "main")


def test_tag_helper_generates_filtered_notes_and_creates_annotated_tag(tmp_path):
    repo, shared_sha = _release_repo(tmp_path)
    command = [
        "python3",
        str(SCRIPT),
        "search",
        "--repo",
        str(repo),
    ]

    preview = _run(repo, *command, "--dry-run")
    assert "feat(search): support multiple backends" in preview.stdout
    assert "fix(shared): adjust retry behavior" not in preview.stdout
    assert "fix(shared): adjust retry behavior" in preview.stderr
    assert "qwen-mm-plugins-search-v1.0.1...qwen-mm-plugins-search-v1.0.2" in preview.stdout
    assert not _git(repo, "tag", "--list", "qwen-mm-plugins-search-v1.0.2").stdout

    created = _run(repo, *command, "--include-shared", shared_sha[:7], "--push")
    assert "Created qwen-mm-plugins-search-v1.0.2" in created.stdout
    assert "Pushed qwen-mm-plugins-search-v1.0.2 to origin" in created.stdout
    assert (
        "refs/tags/qwen-mm-plugins-search-v1.0.2"
        in _git(
            repo,
            "ls-remote",
            "--tags",
            "--refs",
            "origin",
            "refs/tags/qwen-mm-plugins-search-v1.0.2",
        ).stdout
    )
    message = _git(
        repo,
        "for-each-ref",
        "--format=%(contents)",
        "refs/tags/qwen-mm-plugins-search-v1.0.2",
    ).stdout
    assert "feat(search): support multiple backends" in message
    assert "Shared runtime changes:" in message
    assert "fix(shared): adjust retry behavior" in message

    repeated = _run(repo, *command, check=False)
    assert repeated.returncode != 0
    assert "published tags must never move" in repeated.stderr


def test_tag_helper_rejects_release_metadata_drift(tmp_path):
    repo, _ = _release_repo(tmp_path)
    marketplace = json.loads((repo / ".claude-plugin/marketplace.json").read_text())
    marketplace["plugins"][0]["source"]["ref"] = "qwen-mm-plugins-search-v9.9.9"
    (repo / ".claude-plugin/marketplace.json").write_text(json.dumps(marketplace) + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "test: introduce metadata drift")
    _git(repo, "push", "origin", "main")

    result = _run(
        repo,
        "python3",
        str(SCRIPT),
        "search",
        "--repo",
        str(repo),
        "--dry-run",
        check=False,
    )
    assert result.returncode != 0
    assert "does not pin search to qwen-mm-plugins-search-v1.0.2" in result.stderr


def test_tag_helper_supports_initial_skill_only_release(tmp_path):
    repo, _ = _release_repo(tmp_path)
    _add_skill_only_release(repo)

    result = _run(
        repo,
        "python3",
        str(SCRIPT),
        "edu-agent",
        "--repo",
        str(repo),
        "--dry-run",
    )
    assert "qwen-mm-plugins-edu-agent 1.0.0" in result.stdout
    assert "Initial capability release." in result.stdout
    assert "would create qwen-mm-plugins-edu-agent-v1.0.0" in result.stdout
