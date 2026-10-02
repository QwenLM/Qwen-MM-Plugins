"""Release authorization, immutable refs, and harness metadata regression checks (offline)."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("release_bot", ROOT / "scripts/release_bot.py")
bot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bot)


@pytest.mark.parametrize("body", ["/release search=1.2.3", "@github-actions /release search=1.2.3"])
def test_release_command(body):
    assert bot.parse(body) == ("release", {"search": "1.2.3"})
    assert bot.parse("/release framework=1.2.3 mhs=patch")[1] == {"distribution": "1.2.3", "mhs": "patch"}


@pytest.mark.parametrize(
    "body",
    [
        "/release",
        "/publish search=patch",
        "/release search=$(id)",
        "/release search=1.2.3;id",
        "/release framework=patch distribution=minor",
    ],
)
def test_invalid_commands(body):
    with pytest.raises(ValueError):
        bot.parse(body)
    assert bot.parse("please /release search=1.2.3") is None


def test_only_explicit_plugins_release_unless_all_plugins_is_requested():
    index = {"distribution_version": "1.0.0", "plugins": dict.fromkeys(["search", "mhs", "edu-agent"], "1.0.0")}
    assert bot.resolve(index, {"search": "minor"}) == ({"search": "1.1.0"}, "1.0.1")
    assert bot.resolve(index, {"all-plugins": "patch", "search": "minor"}) == (
        {"edu-agent": "1.0.1", "mhs": "1.0.1", "search": "1.1.0"},
        "1.0.1",
    )
    assert bot.resolve(index, {"search": "patch", "distribution": "1.2.0"}) == (
        {"search": "1.0.1"},
        "1.2.0",
    )
    command, requested = bot.parse("/release all-plugins=1.3.0")
    assert command == "release"
    assert bot.resolve(index, requested)[0] == dict.fromkeys(index["plugins"], "1.3.0")
    with pytest.raises(ValueError, match="Select plugins explicitly"):
        bot.resolve(index, {"distribution": "1.2.0"})
    with pytest.raises(ValueError, match="Unknown"):
        bot.resolve(index, {"typo": "patch"})
    with pytest.raises(ValueError, match="must advance"):
        bot.next_version("1.2.3", "1.2.3")
    assert bot.resolve(index, {"edu-agent": "patch"})[0] == {"edu-agent": "1.0.1"}


class FakeAPI:
    repository = "owner/repo"

    def __init__(self, *, permission=True, body="/publish"):
        self.permission = permission
        self.comment = {
            "id": 8,
            "body": body,
            "issue_url": "https://api.github.com/repos/owner/repo/issues/7",
            "user": {"type": "User", "login": "maintainer"},
        }
        self.replies = []

    def call(self, path):
        assert path == "issues/comments/8"
        return self.comment

    def pages(self, path):
        return [self.comment]

    def allowed(self, login):
        return self.permission

    def reply(self, number, body):
        self.replies.append(body)


EVENT = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"id": 8, "body": "/publish"}}


def test_repository_endpoint_has_no_trailing_slash(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "run", lambda repo, *args, **kwargs: calls.append(args) or "{}")
    bot.GitHub("owner/repo").call("")
    assert calls[0][2] == "repos/owner/repo"


def test_unauthorized_comment_cannot_prepare_or_publish(tmp_path):
    assert bot.handle_event(FakeAPI(permission=False), tmp_path, EVENT) == {}


def test_live_comment_wins_over_event_payload(tmp_path):
    assert bot.handle_event(FakeAPI(body="cancelled"), tmp_path, EVENT) == {}
    api = FakeAPI()
    api.comment["issue_url"] = "https://api.github.com/repos/owner/repo/issues/99"
    with pytest.raises(ValueError, match="belong"):
        bot.handle_event(api, tmp_path, EVENT)


def test_publish_rechecks_authorization_before_accessing_candidate(tmp_path):
    with pytest.raises(ValueError, match="authorized"):
        bot.publish(FakeAPI(permission=False), tmp_path, 7, 8, "a" * 40)


def test_queued_request_runs_only_after_merge(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "make_release", lambda *args: calls.append(args[2]) or "prepared")
    api = FakeAPI(body="/release search=patch")
    assert bot.handle_event(api, tmp_path, {"action": "closed", "number": 7, "pull_request": {"merged": False}}) == {}
    assert not calls
    bot.handle_event(api, tmp_path, {"action": "closed", "number": 7, "pull_request": {"merged": True}})
    assert calls == [7]


@pytest.fixture
def release_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Fresh history and no inherited tags: works on a shallow CI checkout and released refs.
    archive = tmp_path / "source.tar"
    subprocess.run(["git", "archive", "HEAD", "-o", str(archive)], cwd=ROOT, check=True)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(repo)], check=True)
    for script in ["prepare_plugin_release.py", "check_manifests.py"]:
        shutil.copyfile(ROOT / "scripts" / script, repo / "scripts" / script)
    bot.git(repo, "init", "--initial-branch=main")
    bot.git(repo, "config", "user.name", "Release Test")
    bot.git(repo, "config", "user.email", "release@example.test")
    bot.git(repo, "add", ".")
    bot.git(repo, "commit", "-m", "source")
    source = bot.git(repo, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    bot.git(repo, "init", "--bare", str(remote))
    bot.git(repo, "push", str(remote), "HEAD:refs/heads/main")
    return repo, source, str(remote)


def test_generated_candidate_preserves_harness_shapes_and_rejects_code_changes(release_repo):
    repo, source, remote = release_repo
    index = json.loads((repo / "plugin-versions.json").read_text())
    releases = {cap: bot.next_version(index["plugins"][cap], "patch") for cap in ["search", "edu-agent"]}
    dist = bot.next_version(index["distribution_version"], "patch")
    bot.prepare(repo, releases, dist, remote)
    head = bot.commit(repo, source, "release")
    info = bot.candidate_info(repo, head, source, remote)
    assert info["plugins"] == releases
    for cap in releases:
        for manifest in [".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".qoder-plugin/plugin.json"]:
            path = f"src/capabilities/{cap}/{manifest}"
            before, after = (json.loads(bot.at(repo, sha, path)) for sha in [source, head])
            assert before.keys() == after.keys()
            for key in before.keys() - {"version", "mcpServers"}:
                assert before[key] == after[key]
    assert not (repo / "src/capabilities/edu-agent/.mcp.json").exists()
    assert (
        bot.at(repo, source, "install.sh").split("CAP_VERSIONS=", 1)[1].split("\n", 1)[1]
        == bot.at(repo, head, "install.sh").split("CAP_VERSIONS=", 1)[1].split("\n", 1)[1]
    )
    (repo / "src/mcp_framework.py").write_text((repo / "src/mcp_framework.py").read_text() + "\n# injected\n")
    bot.git(repo, "add", ".")
    bot.git(repo, "commit", "--amend", "--no-edit")
    with pytest.raises(ValueError, match="beyond generated"):
        bot.candidate_info(repo, bot.git(repo, "rev-parse", "HEAD"), source, remote)


def test_tags_precede_catalog_merge_and_retries_never_move_them(release_repo, tmp_path):
    repo, source, remote = release_repo
    index = json.loads((repo / "plugin-versions.json").read_text())
    releases = {"search": bot.next_version(index["plugins"]["search"], "patch")}
    distribution = bot.next_version(index["distribution_version"], "patch")
    bot.prepare(repo, releases, distribution, remote)
    head = bot.commit(repo, source, "release")
    info = bot.candidate_info(repo, head, source, remote)
    tag = index["tag_format"].format(cap="search", version=releases["search"])
    bot.publish_tags(repo, remote, info, {tag: "Original release notes"})
    assert bot.git(repo, "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}") == "Original release notes"
    before = bot.git(repo, "ls-remote", remote, "refs/tags/*")
    assert bot.git(repo, "ls-remote", remote, "refs/heads/main").split()[0] == source
    bot.publish_tags(repo, remote, info, {tag: "Changed notes on retry"})
    assert bot.git(repo, "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}") == "Original release notes"
    assert bot.git(repo, "ls-remote", remote, "refs/tags/*") == before
    with pytest.raises(ValueError, match="never moved"):
        bot.publish_tags(repo, remote, {**info, "head": source}, {})
    main = tmp_path / "main"
    bot.clone_at(repo, source, main)
    bot.git(main, "config", "user.name", "Release Test")
    bot.git(main, "config", "user.email", "release@example.test")
    (main / "unrelated.txt").write_text("concurrent main update")
    bot.git(main, "add", ".")
    bot.git(main, "commit", "-m", "concurrent code")
    bot.git(main, "fetch", str(repo), head)
    bot.git(main, "merge", "--no-ff", "--no-edit", head)
    bot.git(main, "merge-base", "--is-ancestor", head, "HEAD")
    assert (main / "unrelated.txt").read_text() == "concurrent main update"
    assert bot.git(main, "ls-remote", remote, "refs/tags/*") == before


def test_publish_rejects_a_head_changed_since_verification(tmp_path, monkeypatch):
    class ChangedAPI(FakeAPI):
        def call(self, path):
            if path == "pulls/7":
                return {
                    "base": {"ref": "main", "repo": {"full_name": self.repository}},
                    "head": {
                        "ref": bot.branch_for(7, 8),
                        "sha": "b" * 40,
                        "repo": {"full_name": self.repository},
                    },
                    "state": "open",
                    "merged": False,
                }
            return super().call(path)

    monkeypatch.setattr(bot, "base_state", lambda *args: ("main", "c" * 40, "unused"))
    with pytest.raises(ValueError, match="changed after verification"):
        bot.publish(ChangedAPI(), tmp_path, 7, 8, "a" * 40)


def test_publish_retry_preserves_tags_after_a_failed_merge(release_repo, monkeypatch):
    repo, source, remote = release_repo
    index = json.loads((repo / "plugin-versions.json").read_text())
    releases, distribution = bot.resolve(index, {"search": "patch"})
    bot.prepare(repo, releases, distribution, remote)
    head = bot.commit(repo, source, "release")
    info = {**bot.candidate_info(repo, head, source, remote), "merged": False}
    tag = index["tag_format"].format(cap="search", version=releases["search"])
    monkeypatch.setattr(bot, "release_candidate", lambda *args: (info, remote))

    class MergeAPI(FakeAPI):
        can_merge = False

        def call(self, path, method="GET", data=None):
            if path == "pulls/7/merge":
                assert method == "PUT" and data == {"sha": head, "merge_method": "merge"}
                assert bot.remote_tag_target(repo, remote, tag) == head
                return {"merged": self.can_merge, "message": "Required review pending"}
            return super().call(path)

    monkeypatch.setattr(bot, "release_notes", lambda *args: {tag: "Original release notes"})
    api = MergeAPI()
    with pytest.raises(RuntimeError, match="Tags exist; PR merge needs attention"):
        bot.publish(api, repo, 7, 8, head)
    published = bot.git(repo, "ls-remote", remote, "refs/tags/*")
    assert not api.replies

    def no_regeneration(*args):
        pytest.fail("Published notes must not be regenerated on retry")

    monkeypatch.setattr(bot, "release_notes", no_regeneration)
    api.can_merge = True
    bot.publish(api, repo, 7, 8, head)
    assert bot.git(repo, "ls-remote", remote, "refs/tags/*") == published
    assert "Published immutable tags" in api.replies[0]


def test_shared_detection_ignores_version_stamp_but_finds_runtime_and_dependencies(release_repo):
    repo, source, remote = release_repo
    index = json.loads((repo / "plugin-versions.json").read_text())
    catalog_path = repo / ".claude-plugin/marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    next(p for p in catalog["plugins"] if p["name"] == "qwen-mm-plugins-search")["source"]["url"] = remote
    catalog_path.write_text(json.dumps(catalog))
    source = bot.commit(repo, source, "point test catalog at local remote")
    tag = index["tag_format"].format(cap="search", version=index["plugins"]["search"])
    bot.git(repo, "tag", tag, source)
    bot.git(repo, "push", remote, f"refs/tags/{tag}")
    framework = repo / "src/mcp_framework.py"
    framework.write_text(bot.VERSION.sub('__version__ = "999.0.0"', framework.read_text()))
    stamp = bot.commit(repo, source, "stamp only")
    assert not bot.shared_changed(repo, stamp, {"search"})
    assert bot.shared_notice(repo, stamp, {"search": "999.0.0"}) == ""
    framework.write_text(framework.read_text() + "\n# changed runtime\n")
    runtime = bot.commit(repo, stamp, "runtime")
    assert bot.shared_changed(repo, runtime, {"search"})
    releases, distribution = bot.resolve(index, {"search": "patch"})
    assert set(releases) == {"search"}
    notice = bot.shared_notice(repo, runtime, releases)
    assert "Shared runtime or dependency changes are included" in notice
    assert "`mhs`" in notice and "`edu-agent`" not in notice
    assert "all-plugins=patch" in notice
    assert bot.shared_notice(repo, runtime, {"edu-agent": "999.0.0"}) == ""

    class LocalAPI:
        repository = "owner/repo"
        body = ""

        def pages(self, path):
            return []

        def call(self, path, method="GET", data=None):
            if not path:
                return {"default_branch": "main", "clone_url": remote}
            if path == "pulls/7":
                return {
                    "base": {"ref": "main", "repo": {"full_name": self.repository}},
                    "head": {"ref": "source"},
                    "merged": True,
                    "merge_commit_sha": runtime,
                }
            assert path == "pulls" and method == "POST"
            self.body = data["body"]
            return {"html_url": "https://example.test/version-pr"}

    bot.git(repo, "push", remote, f"{runtime}:refs/heads/main")
    api = LocalAPI()
    reply = bot.make_release(api, repo, 7, {"id": 8}, {"search": "patch"})
    assert notice in api.body and notice in reply
    assert "### Tag notes preview" in api.body
    assert "Release-PR: (this version PR)" in api.body
    assert "Requested-From: https://github.com/owner/repo/pull/7" in api.body
    assert "Shared runtime / dependency changes:" in api.body
    assert f"{runtime[:7]} runtime" in api.body
    assert f"{stamp[:7]} stamp only" not in api.body
    bot.git(repo, "fetch", remote, f"refs/heads/{bot.branch_for(7, 8)}")
    after = json.loads(bot.at(repo, "FETCH_HEAD", "plugin-versions.json"))
    assert after["distribution_version"] == distribution
    assert {cap for cap in index["plugins"] if index["plugins"][cap] != after["plugins"][cap]} == {"search"}
    catalog_after = json.loads(bot.at(repo, "FETCH_HEAD", ".claude-plugin/marketplace.json"))
    for before, after_entry in zip(catalog["plugins"], catalog_after["plugins"], strict=True):
        if before["name"] != "qwen-mm-plugins-search":
            assert before == after_entry
    bot.git(repo, "checkout", "--detach", stamp)
    project = repo / "pyproject.toml"
    project.write_text(project.read_text() + "\n# changed dependencies\n")
    dependencies = bot.commit(repo, stamp, "dependencies")
    assert bot.shared_changed(repo, dependencies, {"search"})


class NotesAPI:
    repository = "owner/repo"

    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.lookups = []

    def pages(self, path):
        assert path.startswith("commits/") and path.endswith("/pulls")
        sha = path.split("/")[1]
        self.lookups.append(sha)
        return self.mapping.get(sha, [])


def associated_pr(number, title, *, repository="owner/repo", merged=True):
    return {
        "number": number,
        "title": title,
        "merged_at": "2026-01-01T00:00:00Z" if merged else None,
        "base": {"repo": {"full_name": repository}},
    }


@pytest.fixture
def notes_repo(release_repo):
    repo, source, remote = release_repo
    catalog_path = repo / ".claude-plugin/marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    for entry in catalog["plugins"]:
        entry["source"]["url"] = remote
    catalog_path.write_text(json.dumps(catalog))
    source = bot.commit(repo, source, "catalog")
    for entry in catalog["plugins"]:
        tag = entry["source"]["ref"]
        bot.git(repo, "tag", tag, source)
        bot.git(repo, "push", remote, f"refs/tags/{tag}")
    return repo, source, remote


def change(repo, path, text, subject):
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text((file.read_text() if file.exists() else "") + text)
    return bot.commit(repo, bot.git(repo, "rev-parse", "HEAD"), subject)


def notes_info(source, plugins):
    return {
        "source": source,
        "plugins": dict.fromkeys(plugins, "9.0.0"),
        "distribution": "9.0.0",
        "tag_format": "qwen-mm-plugins-{cap}-v{version}",
        "requested_from": 500,
    }


def test_notes_scope_deduplication_direct_commits_and_frozen_source(notes_repo):
    repo, baseline, remote = notes_repo
    search = change(repo, "src/capabilities/search/fix.txt", "fix", "not a PR number (#999)")
    rebase = change(repo, "src/capabilities/search/fix.txt", "second", "rebased follow-up")
    # A single PR can contribute to both plugin and shared sections.
    both = change(repo, "src/shared/fix.txt", "shared", "shared follow-up")
    mhs = change(repo, "src/capabilities/mhs/fix.txt", "other", "unrelated plugin")
    direct = change(repo, "pyproject.toml", "\n# new dependency", "direct dependency change (#888)")
    skill = change(repo, "src/capabilities/edu-agent/fix.txt", "skill", "skill change")
    future = change(repo, "src/capabilities/search/fix.txt", "future", "after frozen snapshot")
    api = NotesAPI(
        {
            search: [associated_pr(101, "Search fix")],
            rebase: [associated_pr(101, "Search fix")],
            both: [associated_pr(101, "Search fix")],
        }
    )
    notes = bot.release_notes(api, repo, notes_info(skill, ["search", "edu-agent"]), 600)
    search_notes = notes["qwen-mm-plugins-search-v9.0.0"]
    plugin, shared = search_notes.split("Shared runtime / dependency changes:")
    assert plugin.count("#101 Search fix") == shared.count("#101 Search fix") == 1
    assert f"{direct[:7]} direct dependency change (#888)" in shared
    assert "/pull/888" not in search_notes and "/pull/999" not in search_notes
    assert "Release-PR: https://github.com/owner/repo/pull/600" in search_notes
    assert "Requested-From: https://github.com/owner/repo/pull/500" in search_notes
    assert f"/compare/{baseline}...{skill}" in search_notes
    skill_notes = notes["qwen-mm-plugins-edu-agent-v9.0.0"]
    assert "Shared runtime" not in skill_notes and direct[:7] not in skill_notes
    assert f"{skill[:7]} skill change" in skill_notes
    assert mhs not in api.lookups and future not in api.lookups
    assert len(api.lookups) == len(set(api.lookups))


def test_notes_use_each_catalog_baseline_including_upstream_refs(notes_repo):
    repo, baseline, remote = notes_repo
    index = json.loads(bot.at(repo, baseline, "plugin-versions.json"))
    version = bot.next_version(index["plugins"]["mhs"], "patch")
    first = change(repo, "src/shared/fix.txt", "first", "older shared change")
    # Only mhs already shipped the first shared change. Its previous tag is a side-branch
    # release commit, preserved by the merge commit used by /publish.
    bot.git(repo, "switch", "-c", "previous-mhs-release")
    (repo / "release-marker.txt").write_text("metadata")
    previous_release = bot.commit(repo, first, "previous release")
    previous_tag = index["tag_format"].format(cap="mhs", version=version)
    bot.git(repo, "tag", previous_tag, previous_release)
    bot.git(repo, "push", remote, f"refs/tags/{previous_tag}")
    bot.git(repo, "switch", "main")
    bot.git(repo, "merge", "--no-ff", "--no-edit", "previous-mhs-release")
    catalog_path = repo / ".claude-plugin/marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    next(p for p in catalog["plugins"] if p["name"] == "qwen-mm-plugins-mhs")["source"]["ref"] = previous_tag
    catalog_path.write_text(json.dumps(catalog))
    bot.commit(repo, first, "record previous release")
    latest = change(repo, "src/shared/fix.txt", "second", "new shared change")
    api = NotesAPI({first: [associated_pr(1, "Old shared")], latest: [associated_pr(2, "New shared")]})
    notes = bot.release_notes(api, repo, notes_info(latest, ["search", "mhs"]))
    assert "#1 Old shared" in notes["qwen-mm-plugins-search-v9.0.0"]
    assert "#1 Old shared" not in notes["qwen-mm-plugins-mhs-v9.0.0"]
    assert all("#2 New shared" in note for note in notes.values())
    assert f"/compare/{previous_release}...{latest}" in notes["qwen-mm-plugins-mhs-v9.0.0"]
    assert api.lookups.count(latest) == 1


def test_notes_handle_merge_squash_rebase_and_ignore_generated_metadata(notes_repo):
    repo, baseline, remote = notes_repo
    bot.git(repo, "switch", "-c", "feature")
    child = change(repo, "src/capabilities/search/fix.txt", "feature", "feature commit")
    bot.git(repo, "switch", "main")
    bot.git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
    merge = bot.git(repo, "rev-parse", "HEAD")
    squash = change(repo, "src/capabilities/search/fix.txt", "squashed", "squashed PR")
    rebase1 = change(repo, "src/capabilities/search/fix.txt", "rebase1", "rebased commit one")
    rebase2 = change(repo, "src/capabilities/search/fix.txt", "rebase2", "rebased commit two")
    bot.prepare(repo, {"search": "2.0.0"}, "2.0.0", remote)
    # Retain the old catalog ref for this isolated normalization test.
    (repo / ".claude-plugin/marketplace.json").write_text(bot.at(repo, baseline, ".claude-plugin/marketplace.json"))
    stamp = bot.commit(repo, rebase2, "generated versions only")
    api = NotesAPI(
        {
            merge: [associated_pr(1, "Merged")],
            squash: [associated_pr(2, "Squashed")],
            rebase1: [associated_pr(3, "Rebased")],
            rebase2: [associated_pr(3, "Rebased")],
        }
    )
    note = next(iter(bot.release_notes(api, repo, notes_info(stamp, ["search"])).values()))
    assert all(note.count(f"#{n} ") == 1 for n in [1, 2, 3])
    assert child not in api.lookups and stamp not in api.lookups
    # Other manifest fields still count, even alongside a new version.
    manifest = repo / "src/capabilities/search/.mcp.json"
    data = json.loads(manifest.read_text())
    next(iter(data["mcpServers"].values()))["args"].append("--new-launch-option")
    manifest.write_text(json.dumps(data))
    config = bot.commit(repo, stamp, "launch configuration")
    assert bot.code_changed(repo, config, ("src/capabilities/search",))


def test_unmerged_or_other_repository_prs_are_not_attributed(notes_repo):
    repo, _, _ = notes_repo
    sha = change(repo, "src/capabilities/search/fix.txt", "fix", "direct change")
    api = NotesAPI(
        {sha: [associated_pr(1, "Open", merged=False), associated_pr(2, "Elsewhere", repository="other/repo")]}
    )
    note = next(iter(bot.release_notes(api, repo, notes_info(sha, ["search"])).values()))
    assert f"{sha[:7]} direct change" in note
    assert "#1 Open" not in note and "#2 Elsewhere" not in note


def test_notes_lookup_failure_aborts_before_tag_publication(notes_repo, monkeypatch):
    repo, _, remote = notes_repo
    source = change(repo, "src/capabilities/search/fix.txt", "fix", "fix")
    info = {**notes_info(source, ["search"]), "head": source, "merged": False}
    monkeypatch.setattr(bot, "release_candidate", lambda *args: (info, remote))

    class UnavailableAPI(FakeAPI):
        def pages(self, path):
            raise RuntimeError("GitHub unavailable")

    before = bot.git(repo, "ls-remote", remote, "refs/tags/*")
    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        bot.publish(UnavailableAPI(), repo, 7, 8, source)
    assert bot.git(repo, "ls-remote", remote, "refs/tags/*") == before


def test_preview_contains_literal_notes_even_with_markdown_in_titles():
    preview = bot.notes_preview({"tag": "Release tag\n- #1 ```\n# not a heading"})
    assert "````text\nRelease tag\n- #1 ```\n# not a heading\n````" in preview


def test_publish_writes_generated_notes_at_the_verified_head(notes_repo, monkeypatch):
    repo, _, remote = notes_repo
    source = change(repo, "src/capabilities/search/fix.txt", "fix", "code fix")
    releases, distribution = bot.resolve(json.loads(bot.at(repo, source, "plugin-versions.json")), {"search": "patch"})
    bot.prepare(repo, releases, distribution, remote)
    head = bot.commit(repo, source, "release")
    info = {**bot.candidate_info(repo, head, source, remote), "requested_from": 99, "merged": False}
    monkeypatch.setattr(bot, "release_candidate", lambda *args: (info, remote))
    tag = info["tag_format"].format(cap="search", version=releases["search"])

    class PublishAPI(FakeAPI):
        def pages(self, path):
            assert path == f"commits/{source}/pulls"
            return [associated_pr(99, "Code fix")]

        def call(self, path, method="GET", data=None):
            if path == "pulls/7/merge":
                assert bot.remote_tag_target(repo, remote, tag) == head
                return {"merged": True}
            return super().call(path)

    api = PublishAPI()
    bot.publish(api, repo, 7, 8, head)
    bot.git(repo, "fetch", remote, f"refs/tags/{tag}:refs/tags/{tag}")
    note = bot.git(repo, "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}")
    assert "Release-PR: https://github.com/owner/repo/pull/7" in note
    assert "Requested-From: https://github.com/owner/repo/pull/99" in note
    assert "#99 Code fix (https://github.com/owner/repo/pull/99)" in note
    assert f"Source-Commit: {source}" in note and f"Distribution: {distribution}" in note
    assert api.replies
