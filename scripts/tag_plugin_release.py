#!/usr/bin/env python3
"""Create one capability's annotated release tag on the fetched main commit.

The tag message lists non-merge commits that touched the capability or its cookbook since the
previous capability tag. Shared runtime commits are shown for review and can be included explicitly.
The tag is created locally by default; pass ``--push`` to push only that tag.

Examples:
    python3 scripts/tag_plugin_release.py search
    python3 scripts/tag_plugin_release.py search --include-shared 71ff67e
    python3 scripts/tag_plugin_release.py search --push
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_WEB_URL = "https://github.com/QwenLM/Qwen-MM-Plugins"
SEMVER = re.compile(
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?\Z"
)
SHARED_PATHS = ("src/shared", "src/mcp_framework.py", "pyproject.toml")


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


def run_git(
    repo: Path,
    *args: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise SystemExit(f"git {' '.join(args)} failed: {detail}")
    return result


def read_json_at(repo: Path, target: str, path: str) -> dict:
    result = run_git(repo, "show", f"{target}:{path}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} at {target} is not valid JSON: {exc}") from exc


def path_exists_at(repo: Path, target: str, path: str) -> bool:
    return run_git(repo, "cat-file", "-e", f"{target}:{path}", check=False).returncode == 0


def semver_key(version: str) -> tuple:
    match = SEMVER.fullmatch(version)
    if not match:
        raise ValueError(f"invalid semver: {version}")
    prerelease = match.group("prerelease")
    prerelease_key = (
        tuple((0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".")) if prerelease else ()
    )
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        0 if prerelease else 1,
        prerelease_key,
    )


def verify_release_metadata(repo: Path, target: str, cap: str) -> tuple[str, str]:
    index = read_json_at(repo, target, "plugin-versions.json")
    versions = index.get("plugins", {})
    if cap not in versions:
        choices = ", ".join(sorted(versions))
        raise SystemExit(f"unknown or unpublished capability {cap!r}; choose one of: {choices}")
    version = versions[cap]
    try:
        semver_key(version)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    tag = index["tag_format"].format(cap=cap, version=version)

    marketplace = read_json_at(repo, target, ".claude-plugin/marketplace.json")
    entry = next(
        (item for item in marketplace.get("plugins", []) if item.get("name") == f"qwen-mm-plugins-{cap}"),
        None,
    )
    if entry is None:
        raise SystemExit(f"marketplace at {target} does not list qwen-mm-plugins-{cap}")
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("ref") != tag:
        raise SystemExit(f"marketplace at {target} does not pin {cap} to {tag}")

    cap_root = f"src/capabilities/{cap}"
    for rel in (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        ".qoder-plugin/plugin.json",
    ):
        manifest = read_json_at(repo, target, f"{cap_root}/{rel}")
        if manifest.get("version") != version:
            raise SystemExit(f"{cap_root}/{rel} at {target} is not version {version}")

    for rel in (".claude-plugin/plugin.json", ".mcp.json"):
        path = f"{cap_root}/{rel}"
        if not path_exists_at(repo, target, path):
            continue
        manifest = read_json_at(repo, target, path)
        servers = manifest.get("mcpServers")
        if not servers:
            continue
        args = next(iter(servers.values())).get("args", [])
        if not any(tag in arg for arg in args):
            raise SystemExit(f"{path} at {target} does not launch {tag}")
    return version, tag


def previous_tag(repo: Path, target: str, cap: str, version: str) -> str | None:
    prefix = f"qwen-mm-plugins-{cap}-v"
    candidates: list[tuple[tuple, str]] = []
    current_key = semver_key(version)
    for tag in run_git(repo, "tag", "--list", f"{prefix}*").stdout.splitlines():
        candidate_version = tag.removeprefix(prefix)
        try:
            key = semver_key(candidate_version)
        except ValueError:
            continue
        if key >= current_key:
            continue
        reachable = run_git(repo, "merge-base", "--is-ancestor", f"{tag}^{{}}", target, check=False)
        if reachable.returncode == 0:
            candidates.append((key, tag))
    return max(candidates)[1] if candidates else None


def commits_between(repo: Path, previous: str, target: str, paths: tuple[str, ...]) -> list[Commit]:
    result = run_git(
        repo,
        "log",
        "--reverse",
        "--no-merges",
        "--format=%H%x09%s",
        f"{previous}..{target}",
        "--",
        *paths,
    )
    commits: list[Commit] = []
    for line in result.stdout.splitlines():
        sha, separator, subject = line.partition("\t")
        if separator:
            commits.append(Commit(sha, subject))
    return commits


def resolve_included_shared(repo: Path, requested: list[str], candidates: list[Commit]) -> list[Commit]:
    by_sha = {commit.sha: commit for commit in candidates}
    included: list[Commit] = []
    for value in requested:
        sha = run_git(repo, "rev-parse", f"{value}^{{commit}}").stdout.strip()
        if sha not in by_sha:
            raise SystemExit(f"--include-shared {value}: not a shared-only commit in this release range")
        if by_sha[sha] not in included:
            included.append(by_sha[sha])
    return included


def render_notes(
    cap: str,
    version: str,
    tag: str,
    previous: str | None,
    capability_commits: list[Commit],
    included_shared: list[Commit],
) -> str:
    lines = [f"qwen-mm-plugins-{cap} {version}", ""]
    if previous is None:
        lines.append("Initial capability release.")
    else:
        lines.extend([f"Changes since {previous}:", ""])
        lines.extend(f"- {commit.subject} ({commit.short_sha})" for commit in capability_commits)
        if included_shared:
            lines.extend(["", "Shared runtime changes:", ""])
            lines.extend(f"- {commit.subject} ({commit.short_sha})" for commit in included_shared)
        lines.extend(["", f"Compare: {REPO_WEB_URL}/compare/{previous}...{tag}"])
    return "\n".join(lines).rstrip() + "\n"


def remote_tag_exists(repo: Path, remote: str, tag: str) -> bool:
    result = run_git(repo, "ls-remote", "--tags", "--refs", remote, f"refs/tags/{tag}")
    return bool(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capability")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--include-shared", action="append", default=[], metavar="COMMIT")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print notes and checks without creating the tag",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="push the newly-created tag to the selected remote",
    )
    parser.add_argument("--no-fetch", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repo", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.dry_run and args.push:
        parser.error("--dry-run and --push cannot be used together")

    repo = args.repo.expanduser().resolve(strict=True)
    inside = run_git(repo, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode or inside.stdout.strip() != "true":
        parser.error(f"--repo is not a Git checkout: {repo}")
    if not args.no_fetch:
        run_git(
            repo,
            "fetch",
            args.remote,
            f"refs/heads/{args.branch}:refs/remotes/{args.remote}/{args.branch}",
            "--tags",
        )

    target_ref = f"refs/remotes/{args.remote}/{args.branch}"
    target = run_git(repo, "rev-parse", f"{target_ref}^{{commit}}").stdout.strip()
    version, tag = verify_release_metadata(repo, target, args.capability)
    if run_git(repo, "tag", "--list", tag).stdout.strip():
        raise SystemExit(f"tag already exists locally: {tag}; published tags must never move")
    if remote_tag_exists(repo, args.remote, tag):
        raise SystemExit(f"tag already exists on {args.remote}: {tag}; published tags must never move")

    previous = previous_tag(repo, target, args.capability, version)
    capability_commits: list[Commit] = []
    shared_candidates: list[Commit] = []
    if previous:
        capability_paths = (
            f"src/capabilities/{args.capability}",
            f"cookbooks/{args.capability}",
        )
        capability_commits = commits_between(repo, previous, target, capability_paths)
        if not capability_commits:
            raise SystemExit(f"no {args.capability} commits found between {previous} and {target[:7]}")
        capability_shas = {commit.sha for commit in capability_commits}
        shared_candidates = [
            commit
            for commit in commits_between(repo, previous, target, SHARED_PATHS)
            if commit.sha not in capability_shas
        ]
    included_shared = resolve_included_shared(repo, args.include_shared, shared_candidates)
    notes = render_notes(
        args.capability,
        version,
        tag,
        previous,
        capability_commits,
        included_shared,
    )

    print(notes, end="")
    if shared_candidates:
        print(
            "\nShared/runtime commits in the range were not included automatically:",
            file=sys.stderr,
        )
        for commit in shared_candidates:
            marker = "included" if commit in included_shared else "review"
            print(f"  [{marker}] {commit.short_sha} {commit.subject}", file=sys.stderr)
        print(
            "Re-run with --include-shared <sha> for each one relevant to this capability.",
            file=sys.stderr,
        )

    if args.dry_run:
        print(f"\nDry run: would create {tag} at {target}")
        return 0
    run_git(repo, "tag", "-a", tag, target, "-F", "-", input_text=notes)
    print(f"\nCreated {tag} at {target}")
    if args.push:
        run_git(repo, "push", args.remote, f"refs/tags/{tag}")
        print(f"Pushed {tag} to {args.remote}")
    else:
        print(f"Inspect: git show {tag}")
        print(f"Push:    git push {args.remote} refs/tags/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
