"""Unlisted code → first release → installed catalog, using offline Git remotes."""

import json
import os
import shutil
import subprocess
import sys

import pytest
import test_repo_sync as sync
from test_release_bot import FakeAPI, NotesAPI, associated_pr, bot, change
from test_release_bot import release_repo as release_repo


def add_plugin(repo, cap, *, server):
    target = repo / "src/capabilities" / cap
    template = repo / "src/capabilities" / ("example" if server else "edu-agent")
    target.mkdir()
    for folder in [".claude-plugin", ".codex-plugin", ".qoder-plugin"]:
        shutil.copytree(template / folder, target / folder)
    (target / "skill").mkdir()
    (target / "skill/SKILL.md").write_text(
        f"---\nname: qwen-mm-plugins-{cap}\ndescription: New plugin.\n---\nA skill.\n"
    )
    if server:
        shutil.copyfile(template / ".mcp.json", target / ".mcp.json")
        package = "qwen_mm_plugins_" + cap.replace("-", "_")
        shutil.copytree(
            template / "qwen_mm_plugins_example", target / package, ignore=shutil.ignore_patterns("__pycache__")
        )
        project = repo / "pyproject.toml"
        text = project.read_text()
        for table, entry in {
            "project.optional-dependencies": f"{cap} = []",
            "project.scripts": f'qwen-mm-plugins-{cap} = "{package}.__main__:main"',
            "tool.setuptools.package-dir": f'{package} = "src/capabilities/{cap}/{package}"',
        }.items():
            text = text.replace(f"[{table}]\n", f"[{table}]\n{entry}\n")
        text = text.replace('all = ["qwen-mm-plugins[', f'all = ["qwen-mm-plugins[{cap},')
        text = text.replace("where = [", f'where = ["src/capabilities/{cap}", ')
        project.write_text(text)
    for path in target.rglob("*.json"):
        text = path.read_text().replace("qwen-mm-plugins-example", f"qwen-mm-plugins-{cap}")
        text = text.replace("[example]", f"[{cap}]").replace("qwen-mm-plugins-edu-agent", f"qwen-mm-plugins-{cap}")
        data = json.loads(text)
        if path.name == "plugin.json":
            # The first version is independent of this template value.
            data["version"] = "9.0.0"
            data["description"] = f"Develop {cap}."
        path.write_text(json.dumps(data))
    return target


@pytest.fixture
def unpublished_repo(release_repo):
    repo, source, remote = release_repo
    add_plugin(repo, "new-server", server=True)
    add_plugin(repo, "new-skill", server=False)
    source = bot.commit(repo, source, "add unpublished plugins")
    bot.git(repo, "push", remote, "HEAD:refs/heads/main")
    return repo, source, remote


def test_unpublished_code_passes_checks_without_install_registration(unpublished_repo, monkeypatch):
    repo, source, _ = unpublished_repo
    assert "new-server" in bot.source_plugins(repo, source)
    assert "example" not in bot.source_plugins(repo, source)
    output = bot.run(repo, sys.executable, "scripts/check_manifests.py")
    assert "unpublished; excluded from stable installs" in output
    monkeypatch.setattr(sync, "_ROOT", repo)
    monkeypatch.setattr(sync, "_CAPS_DIR", repo / "src/capabilities")
    sync.test_marketplace_lists_only_published_capabilities()
    for cap in ["new-server", "new-skill"]:
        sync.test_manifest_name_and_version_agree(cap)
        sync.test_manifests_bundle_every_component_the_capability_ships(cap)
    sync.test_mcp_launch_spec_agrees("new-server")
    assert "new-server" not in (repo / "plugin-versions.json").read_text()
    assert "new-server" not in (repo / ".claude-plugin/marketplace.json").read_text()
    assert "new-server" not in (repo / "install.sh").read_text()


def test_first_release_requires_explicit_selection_and_version():
    index = {"plugins": {"search": "1.0.0"}, "distribution_version": "1.0.0"}
    available = {"new-server", "new-skill"}
    assert bot.resolve(index, {"all-plugins": "patch"}, available)[0] == {"search": "1.0.1"}
    assert bot.resolve(index, {"all-plugins": "patch", "new-server": "0.1.0"}, available)[0] == {
        "search": "1.0.1",
        "new-server": "0.1.0",
    }
    for level in ["patch", "minor", "major"]:
        with pytest.raises(ValueError, match="First release.*explicit version"):
            bot.resolve(index, {"new-server": level}, available)
    for cap in ["typo", "example"]:
        with pytest.raises(ValueError, match="Unknown plugins"):
            bot.resolve(index, {cap: "1.0.0"}, available)


def installer_catalog(repo):
    # --help only loads helpers; environment and configuration stay in the temporary checkout.
    script = """source ./install.sh --help >/dev/null
for ((i=0; i<${#CAP_ITEMS[@]}; i++)); do
  kind=mcp; is_skill_only "${CAP_ITEMS[$i]}" && kind=skill
  printf '%s\\t%s\\t%s\\t%s\\n' "${CAP_ITEMS[$i]}" "${CAP_VERSIONS[$i]}" "$kind" "${CAP_DESC[$i]}"
done"""
    env = dict(os.environ, QMP_CONFIG_DIR=str(repo / "test-config"), QMP_REPO="https://example.test/repo.git")
    result = subprocess.run(["bash", "-c", script], cwd=repo, env=env, text=True, capture_output=True, check=True)
    return {parts[0]: parts[1:] for line in result.stdout.splitlines() if (parts := line.split("\t", 3))}


def test_prepare_adds_only_selected_plugins_and_preserves_installer_behavior(unpublished_repo):
    repo, source, remote = unpublished_repo
    before = json.loads(bot.at(repo, source, "plugin-versions.json"))
    old_catalog = json.loads(bot.at(repo, source, ".claude-plugin/marketplace.json"))["plugins"]
    old_installer = installer_catalog(repo)
    releases, distribution = bot.resolve(
        before, {"new-server": "0.1.0", "new-skill": "1.0.0"}, bot.source_plugins(repo, source)
    )
    bot.prepare(repo, releases, distribution, remote)
    head = bot.commit(repo, source, "first releases")
    info = bot.candidate_info(repo, head, source, remote)
    assert info["plugins"] == releases
    catalog = json.loads((repo / ".claude-plugin/marketplace.json").read_text())["plugins"]
    assert catalog[: len(old_catalog)] == old_catalog
    assert {entry["name"] for entry in catalog[len(old_catalog) :]} == {f"qwen-mm-plugins-{cap}" for cap in releases}
    installed = installer_catalog(repo)
    assert {cap: installed[cap] for cap in old_installer} == old_installer
    assert installed["new-server"] == ["0.1.0", "mcp", "Develop new-server."]
    assert installed["new-skill"] == ["1.0.0", "skill", "Develop new-skill."]
    assert "example" not in installed
    for cap, version in releases.items():
        entry = next(p for p in catalog if p["name"] == f"qwen-mm-plugins-{cap}")
        assert entry["source"]["url"] == remote
        assert entry["source"]["ref"] == f"qwen-mm-plugins-{cap}-v{version}"
        for harness in ["claude", "codex", "qoder"]:
            manifest = json.loads((repo / f"src/capabilities/{cap}/.{harness}-plugin/plugin.json").read_text())
            assert manifest["version"] == version
    assert not (repo / "src/capabilities/new-skill/.mcp.json").exists()
    server = repo / "src/capabilities/new-server/qwen_mm_plugins_new_server/__init__.py"
    assert '__version__ = "0.1.0"' in server.read_text()
    # Existing local/restore rewriting also works for the newly published registrations.
    bot.run(repo, sys.executable, "scripts/rewrite_plugin_sources.py", "--repo", str(repo), "new-server", "new-skill")
    bot.run(
        repo,
        sys.executable,
        "scripts/rewrite_plugin_sources.py",
        "--repo",
        str(repo),
        "--restore",
        "new-server",
        "new-skill",
    )
    bot.run(repo, sys.executable, "scripts/check_manifests.py")


def test_initial_notes_include_development_prs_but_no_unrelated_shared_history(unpublished_repo):
    repo, first, _ = unpublished_repo
    shared = change(repo, "src/shared/other.txt", "another change", "shared change")
    source = change(repo, "src/capabilities/new-server/skill/SKILL.md", "refinement", "refine before release")
    api = NotesAPI({first: [associated_pr(10, "New plugins")], source: [associated_pr(11, "Refine server")]})
    info = {
        "source": source,
        "plugins": {"new-server": "1.0.0", "new-skill": "1.0.0"},
        "distribution": "1.1.10",
        "tag_format": "qwen-mm-plugins-{cap}-v{version}",
        "requested_from": 11,
    }
    notes = bot.release_notes(api, repo, info)
    for note in notes.values():
        assert "Previous-Tag: none (initial release)" in note
        assert "#10 New plugins" in note and "Source-Tree:" in note
        assert "Compare:" not in note
    server = notes["qwen-mm-plugins-new-server-v1.0.0"]
    skill = notes["qwen-mm-plugins-new-skill-v1.0.0"]
    assert "#11 Refine server" in server and "Initial snapshot" in server
    assert "#11 Refine server" not in skill and "Shared runtime" not in skill
    assert shared not in api.lookups and api.lookups.count(first) == 1
    assert bot.shared_notice(repo, source, info["plugins"]) == ""


@pytest.mark.parametrize("tamper", ["code", "installer", "new-code", "remove-published"])
def test_first_candidate_rejects_changes_beyond_generated_registration(unpublished_repo, tamper):
    repo, source, remote = unpublished_repo
    index = json.loads(bot.at(repo, source, "plugin-versions.json"))
    distribution = bot.next_version(index["distribution_version"], "patch")
    bot.prepare(repo, {"new-skill": "1.0.0"}, distribution, remote)
    if tamper == "code":
        (repo / "src/capabilities/new-skill/skill/SKILL.md").write_text("injected code")
    elif tamper == "installer":
        with (repo / "install.sh").open("a") as stream:
            stream.write("\n# unrelated launcher change\n")
    else:
        path = repo / "plugin-versions.json"
        data = json.loads(path.read_text())
        if tamper == "new-code":
            add_plugin(repo, "injected", server=False)
            data["plugins"]["injected"] = "1.0.0"
        else:
            del data["plugins"]["core"]
        path.write_text(json.dumps(data))
    head = bot.commit(repo, source, "tampered candidate")
    with pytest.raises(ValueError, match="beyond generated|Unknown plugins|cannot remove"):
        bot.candidate_info(repo, head, source, remote)


def test_existing_plugin_missing_tag_is_not_treated_as_initial(unpublished_repo):
    repo, source, remote = unpublished_repo
    catalog_path = repo / ".claude-plugin/marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    next(p for p in catalog["plugins"] if p["name"] == "qwen-mm-plugins-search")["source"]["url"] = remote
    catalog_path.write_text(json.dumps(catalog))
    source = bot.commit(repo, source, "missing baseline in local test remote")
    info = {
        "source": source,
        "plugins": {"search": "1.2.0"},
        "distribution": "1.2.0",
        "tag_format": "qwen-mm-plugins-{cap}-v{version}",
        "requested_from": 7,
    }
    with pytest.raises(RuntimeError, match="remote ref"):
        bot.release_notes(NotesAPI(), repo, info)


def test_installer_treats_manifest_description_as_literal_data(unpublished_repo):
    repo, source, remote = unpublished_repo
    index = json.loads(bot.at(repo, source, "plugin-versions.json"))
    distribution = bot.next_version(index["distribution_version"], "patch")
    marker = repo / "must-not-exist"
    description = f"""中文 quotes " ' slash \\1 $HOME $(touch {marker}) `touch {marker}`"""
    path = repo / "src/capabilities/new-skill/.claude-plugin/plugin.json"
    data = json.loads(path.read_text())
    data["description"] = description
    path.write_text(json.dumps(data))
    bot.prepare(repo, {"new-skill": "1.0.0"}, distribution, remote)
    assert installer_catalog(repo)["new-skill"][2] == description
    assert not marker.exists()


def test_first_release_event_publishes_tags_before_registering_main_and_can_retry(unpublished_repo):
    repo, source, remote = unpublished_repo
    tag = "qwen-mm-plugins-new-skill-v1.0.0"
    head = None

    class LocalAPI(FakeAPI):
        def pages(self, path):
            if path == f"commits/{source}/pulls":
                return [associated_pr(7, "New plugins")]
            return []

        def call(self, path, method="GET", data=None):
            nonlocal head
            if not path:
                return {"default_branch": "main", "clone_url": remote}
            if path == "pulls/7":
                return {
                    "base": {"ref": "main", "repo": {"full_name": self.repository}},
                    "head": {"ref": "code"},
                    "merged": True,
                    "merge_commit_sha": source,
                }
            if path == "pulls":
                assert method == "POST" and "initial release" in data["body"]
                assert "#7 New plugins" in data["body"]
                assert bot.remote_tag_target(repo, remote, tag) is None
                bot.git(repo, "fetch", remote, f"refs/heads/{data['head']}")
                head = bot.git(repo, "rev-parse", "FETCH_HEAD")
                return {"html_url": "https://github.com/owner/repo/pull/9"}
            if path == "pulls/9":
                return {
                    "base": {"ref": "main", "repo": {"full_name": self.repository}},
                    "head": {"ref": bot.branch_for(7, 8), "sha": head, "repo": {"full_name": self.repository}},
                    "merged": False,
                    "state": "open",
                }
            if path == "pulls/9/merge":
                assert bot.remote_tag_target(repo, remote, tag) == head
                assert "new-skill" not in json.loads(bot.at(repo, "main", "plugin-versions.json"))["plugins"]
                if not self.can_merge:
                    return {"merged": False, "message": "Waiting for review"}
                bot.git(repo, "merge", "--no-ff", "--no-edit", head)
                bot.git(repo, "push", remote, "HEAD:refs/heads/main")
                return {"merged": True}
            return super().call(path)

    api = LocalAPI(body="/release new-skill=1.0.0")
    api.can_merge = False
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"id": 8}}
    assert bot.handle_event(api, repo, event) == {}
    assert head and "Version PR ready" in api.replies[0]
    api.comment.update(body="/publish", issue_url="https://api.github.com/repos/owner/repo/issues/9")
    event["issue"]["number"] = 9
    assert bot.handle_event(api, repo, event)["head"] == head
    with pytest.raises(RuntimeError, match="merge needs attention"):
        bot.publish(api, repo, 9, 8, head)
    published = bot.git(repo, "ls-remote", remote, f"refs/tags/{tag}")
    api.can_merge = True
    bot.publish(api, repo, 9, 8, head)
    assert bot.git(repo, "ls-remote", remote, f"refs/tags/{tag}") == published
    index = json.loads((repo / "plugin-versions.json").read_text())
    assert index["plugins"]["new-skill"] == "1.0.0" and "new-server" not in index["plugins"]
    assert bot.resolve(index, {"new-skill": "patch"})[0] == {"new-skill": "1.0.1"}
    assert bot.git(repo, "rev-parse", "HEAD") != head  # merged with its own merge commit
