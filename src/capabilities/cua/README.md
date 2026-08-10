# qwen-mm-plugins-cua

Computer-use (CUA) for the local desktop, as a **passthrough** to
[trycua/cua](https://github.com/trycua/cua)'s **Cua Driver** (MIT). This capability ships
no code of its own — it registers the external `cua-driver` binary as the MCP server
`cua-computer-use` and provides a thin skill covering install + usage.

## Shape

- `skill/SKILL.md` — when to use it, the install prerequisite, macOS permissions, safety.
- `.claude-plugin/plugin.json` — skill + inline `mcpServers` → `cua-driver mcp`.
- `.codex-plugin/plugin.json` + `.mcp.json` — same for codex.
- **No** `qwen_mm_plugins_cua/` Python package, **no** pyproject entry, **no** extras group:
  the MCP server is the external `cua-driver` binary, not an in-repo uvx server. (This is
  why the capability is manifest-only, unlike blender/freecad which ship a Python client.)

## Prerequisite

`cua-driver` must be installed on the machine (it is not pulled in by `uvx`):

```bash
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
# macOS: open -n -g -a CuaDriver --args serve && cua-driver permissions grant
```

The plugin.json assumes `cua-driver` is on `PATH` (the installer sets this up). If a user
installs it elsewhere, they can override with an absolute `command` path, or use
`cua-driver mcp-config --client claude` to register it directly.

## Why passthrough (option B), not a vendored/self-written server

- Cua Driver is a self-contained native (Rust) binary with its own MCP server, CLI, and
  Claude Code skill — there is no Python source worth vendoring, and its Swift/Rust stack
  does not fit this repo's one-Python-wheel + uvx model.
- The window/accessibility-tree action space it already provides is more robust than a
  screenshot+coordinate approach, so re-implementing in-repo adds little.

## Not done / open

- The `command` is `cua-driver` (PATH-relative). Consider documenting the absolute-path
  fallback for non-standard installs in `docs/en/installation.md`.
- Optional: a startup preflight that detects a missing `cua-driver` and prints the install
  hint (currently surfaced only when a tool call fails).
