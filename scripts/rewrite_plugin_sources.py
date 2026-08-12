#!/usr/bin/env python3
"""Point selected plugin sources at a local Qwen-MM-Plugins checkout."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_URL = "https://github.com/QwenLM/Qwen-MM-Plugins.git"


def _write_if_changed(path: Path, data: dict, original: str) -> bool:
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if rendered == original:
        return False
    path.write_text(rendered)
    return True


def _rewrite_marketplace(
    path: Path,
    capabilities: set[str],
    *,
    restore: bool,
    versions: dict[str, str],
) -> bool:
    original = path.read_text()
    data = json.loads(original)
    found: set[str] = set()
    for plugin in data.get("plugins", []):
        name = plugin.get("name", "")
        if not name.startswith("qwen-mm-plugins-"):
            continue
        cap = name.removeprefix("qwen-mm-plugins-")
        if cap in capabilities:
            plugin["source"] = (
                {
                    "source": "git-subdir",
                    "url": REPO_URL,
                    "path": f"src/capabilities/{cap}",
                    "ref": f"qwen-mm-plugins-{cap}-v{versions[cap]}",
                }
                if restore
                else f"./src/capabilities/{cap}"
            )
            found.add(cap)
    missing = capabilities - found
    if missing:
        raise ValueError(f"capabilities missing from marketplace: {', '.join(sorted(missing))}")
    return _write_if_changed(path, data, original)


def _rewrite_mcp(path: Path, cap: str, source: str, *, refresh: bool) -> bool:
    original = path.read_text()
    pattern = re.compile(rf"(qwen-mm-plugins\[{re.escape(cap)}\] @ )[^\"\r\n]+")
    rendered, count = pattern.subn(rf"\g<1>{source}", original)
    if count == 0:
        raise ValueError(f"no qwen-mm-plugins[{cap}] source found")

    if refresh and '"--refresh"' not in rendered:
        source_pos = rendered.index(f"qwen-mm-plugins[{cap}] @ ")
        args_start = rendered.rfind("[", 0, source_pos)
        if args_start < 0:
            raise ValueError("could not locate MCP args array")
        following = rendered[args_start + 1 :]
        newline = re.match(r"(\r?\n)([ \t]*)", following)
        insertion = f'{newline.group(1)}{newline.group(2)}"--refresh",' if newline else '"--refresh", '
        rendered = rendered[: args_start + 1] + insertion + following
    elif not refresh:
        rendered = re.sub(r'(?m)^[ \t]*"--refresh",[ \t]*\r?\n', "", rendered)
        rendered = re.sub(r'"--refresh",[ \t]*', "", rendered)

    if rendered == original:
        return False
    path.write_text(rendered)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--restore", action="store_true")
    parser.add_argument("capabilities", nargs="+")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve(strict=True)
    if not (repo / "pyproject.toml").is_file():
        parser.error(f"--repo must be the repository root containing pyproject.toml: {repo}")

    version_data = json.loads((repo / "plugin-versions.json").read_text())
    versions: dict[str, str] = version_data["plugins"]
    capabilities = set(versions) if args.capabilities == ["all"] else set(args.capabilities)
    unknown = capabilities - set(versions)
    if unknown:
        parser.error(f"unknown capabilities: {', '.join(sorted(unknown))}")
    marketplace = repo / ".claude-plugin/marketplace.json"
    paths: list[tuple[Path, str | None]] = [(marketplace, None)]
    for cap in sorted(capabilities):
        cap_dir = repo / "src/capabilities" / cap
        mcp_manifest = cap_dir / ".mcp.json"
        if not mcp_manifest.is_file():
            continue
        paths.extend(
            (path, cap)
            for path in (cap_dir / ".claude-plugin/plugin.json", mcp_manifest)
            if path.is_file()
        )

    for path, cap in paths:
        try:
            changed = (
                _rewrite_marketplace(
                    path,
                    capabilities,
                    restore=args.restore,
                    versions=versions,
                )
                if path == marketplace
                else _rewrite_mcp(
                    path,
                    cap,
                    (f"git+{REPO_URL}@qwen-mm-plugins-{cap}-v{versions[cap]}" if args.restore else repo.as_uri()),
                    refresh=args.refresh,
                )
            )
        except (ValueError, json.JSONDecodeError) as exc:
            parser.error(f"{path}: {exc}")
        print(f"{'rewrote' if changed else 'unchanged'}\t{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
