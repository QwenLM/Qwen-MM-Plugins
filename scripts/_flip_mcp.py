#!/usr/bin/env python3
"""Rewrite every plugin manifest under the given paths so its uvx --from points at a local
file:// URL instead of git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main.

Shared between:
- `scripts/dev-plugin.sh` (dev-time, single cap, optionally adds `--refresh` so uvx rebuilds
  the local package on every launch)
- `bash install.sh localize` (production-time, all caps, no `--refresh`)

Driven by env vars:
- `REPO`            absolute path to the local checkout (required; the script URL-encodes it)
- `FLIP_ADD_REFRESH` set to "1" to insert `uvx --refresh` at the head of args (dev-time only —
  production localize must leave it off so the uvx cache is shared with `git+https://` users)

CLI: _flip_mcp.py <manifest.json> [<manifest.json> ...]
Exits 0 on success, 1 on any IO/JSON error (the caller surfaces per-file status).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.parse

GIT_REF = "git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main"


def _local_url(repo: str) -> str:
    # PEP-508 requires percent-escaping inside file:// URLs (spaces, #, %).
    return "file://" + urllib.parse.quote(pathlib.Path(repo).resolve().as_posix(), safe="/:@!")


def _flip(path: str) -> str:
    """Flip one manifest in place. Returns "ok" / "unchanged" / "skip" / "fail:<reason>"."""
    p = pathlib.Path(path)
    if not p.is_file():
        return "skip:not-found"
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"fail:parse:{exc}"
    changed = False
    for srv in data.get("mcpServers", {}).values():
        args = srv.get("args", [])
        new_args = [a.replace(GIT_REF, _local_url(os.environ["REPO"])) for a in args]
        if new_args == args and "--refresh" not in args and not os.environ.get("FLIP_ADD_REFRESH"):
            # Already pointing at this local path AND no refresh requested → idempotent no-op.
            continue
        if os.environ.get("FLIP_ADD_REFRESH") == "1" and "--refresh" not in new_args:
            new_args.insert(0, "--refresh")
        srv["args"] = new_args
        changed = True
    if not changed:
        return "unchanged"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return "ok"


def main() -> int:
    if "REPO" not in os.environ:
        print("_flip_mcp: REPO env var is required", file=sys.stderr)
        return 1
    rc = 0
    for path in sys.argv[1:]:
        status = _flip(path)
        print(f"{status}\t{path}")
        if status.startswith("fail"):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
