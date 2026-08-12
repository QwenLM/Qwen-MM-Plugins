#!/usr/bin/env python3
"""Prepare one capability release; never commits, tags, or pushes.

Usage:
    python scripts/prepare_plugin_release.py search 1.1.0 --distribution-version 1.0.2

The command updates every version/ref surface that belongs to the capability. Commit the resulting
code and metadata together, run the tests, then create the printed annotated tag on that final
commit. Multiple capabilities may be prepared before one weekly release commit; create one tag per
prepared capability, and let those tags point to the same commit.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPS = ROOT / "src" / "capabilities"
INDEX = ROOT / "plugin-versions.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
INSTALLER = ROOT / "install.sh"
FRAMEWORK = ROOT / "src" / "mcp_framework.py"
REPO_URL = "https://github.com/QwenLM/Qwen-MM-Plugins.git"
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\Z")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"{path.relative_to(ROOT)}: expected one match for {pattern!r}, got {count}")
    path.write_text(updated, encoding="utf-8")


def update_installer(cap: str, version: str) -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    caps_match = re.search(r"^CAP_ITEMS=\(([^\n]+)\)$", text, re.MULTILINE)
    versions_match = re.search(r"^CAP_VERSIONS=\(([^\n]+)\)$", text, re.MULTILINE)
    if not caps_match or not versions_match:
        raise SystemExit("install.sh: CAP_ITEMS/CAP_VERSIONS must each stay on one line")
    caps = caps_match.group(1).split()
    versions = versions_match.group(1).split()
    if len(caps) != len(versions) or cap not in caps:
        raise SystemExit("install.sh: capability/version arrays are inconsistent")
    versions[caps.index(cap)] = version
    replacement = f"CAP_VERSIONS=({' '.join(versions)})"
    INSTALLER.write_text(text[: versions_match.start()] + replacement + text[versions_match.end() :], encoding="utf-8")


def update_distribution_version(index: dict, marketplace: dict, version: str) -> None:
    index["distribution_version"] = version
    replace_once(FRAMEWORK, r'^__version__ = "[^"]+"', f'__version__ = "{version}"')
    metadata = marketplace.get("metadata")
    if not isinstance(metadata, dict):
        raise SystemExit("marketplace: metadata must be an object")
    metadata["version"] = version


def update_manifests(cap: str, version: str, tag: str) -> None:
    cap_dir = CAPS / cap
    for rel in (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        ".qoder-plugin/plugin.json",
    ):
        path = cap_dir / rel
        data = load(path)
        data["version"] = version
        if rel == ".claude-plugin/plugin.json" and "mcpServers" in data:
            server = next(iter(data["mcpServers"].values()))
            from_index = server["args"].index("--from") + 1
            server["args"][from_index] = f"qwen-mm-plugins[{cap}] @ git+{REPO_URL}@{tag}"
        dump(path, data)

    mcp_path = cap_dir / ".mcp.json"
    if mcp_path.exists():
        data = load(mcp_path)
        server = next(iter(data["mcpServers"].values()))
        from_index = server["args"].index("--from") + 1
        server["args"][from_index] = f"qwen-mm-plugins[{cap}] @ git+{REPO_URL}@{tag}"
        dump(mcp_path, data)

    packages = [p for p in cap_dir.iterdir() if p.is_dir() and p.name.isidentifier() and (p / "__init__.py").is_file()]
    if len(packages) > 1:
        raise SystemExit(f"{cap}: found multiple server packages")
    if packages:
        replace_once(packages[0] / "__init__.py", r'^__version__ = "[^"]+"$', f'__version__ = "{version}"')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capability")
    parser.add_argument("version")
    parser.add_argument(
        "--distribution-version",
        required=True,
        help="new one-distribution/release-train semver; reuse it for every cap in one release commit",
    )
    args = parser.parse_args()
    cap, version = args.capability, args.version
    if not SEMVER.fullmatch(version):
        parser.error(f"version must be semver, got {version!r}")
    if not SEMVER.fullmatch(args.distribution_version):
        parser.error(f"distribution version must be semver, got {args.distribution_version!r}")

    index = load(INDEX)
    versions = index["plugins"]
    if cap not in versions:
        parser.error(f"unknown or unpublished capability {cap!r}; choose one of: {', '.join(sorted(versions))}")
    tag = index["tag_format"].format(cap=cap, version=version)
    existing = subprocess.run(
        ["git", "tag", "--list", tag], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if existing:
        parser.error(f"tag already exists locally: {tag}")

    market = load(MARKETPLACE)
    entry = next((p for p in market["plugins"] if p["name"] == f"qwen-mm-plugins-{cap}"), None)
    if entry is None:
        raise SystemExit(f"marketplace: missing qwen-mm-plugins-{cap}")
    versions[cap] = version
    update_distribution_version(index, market, args.distribution_version)
    entry["source"] = {
        "source": "git-subdir",
        "url": REPO_URL,
        "path": f"src/capabilities/{cap}",
        "ref": tag,
    }
    dump(INDEX, index)
    update_manifests(cap, version, tag)
    update_installer(cap, version)
    dump(MARKETPLACE, market)

    print(f"Prepared qwen-mm-plugins-{cap} {version}")
    print(f"Distribution release train: {args.distribution_version}")
    print("Next: run checks, commit all code + metadata, then create the immutable tag:")
    print(f"  git tag -a {tag} -m 'qwen-mm-plugins-{cap} {version}'")
    print("This script did not commit, tag, or push anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
