# qwen-mm-plugins-cua

Computer-use (CUA) for the local desktop, as a **passthrough** to
[trycua/cua](https://github.com/trycua/cua)'s **Cua Driver** (MIT). This capability ships
no code of its own — it registers the external `cua-driver` binary as the MCP server
`cua-computer-use` and provides a thin skill covering install + usage.

## Shape

- `skill/SKILL.md` — a **thin router**: when to use it, the install prerequisite, and the core
  invariants (snapshot-before / verify-after, narrowest-route-first, no-foreground, `element_token`
  over pixels, coordinates are pixels not 0–1000). It defers to cua's own authoritative multi-file
  skill pack (`SKILL.md` + `MACOS/WINDOWS/LINUX/BROWSER.md`, frontmatter `name: cua-driver`),
  installed via `cua-driver skills install`. We intentionally do **not** duplicate that pack — ours
  just routes to it and keeps behaviour safe before it's loaded.
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

## Verified

`cua-driver mcp` was installed (v0.19.3, linux-x86_64) and probed end-to-end:
`initialize` returns a valid result (protocol `2025-06-18`, serverInfo `cua-driver 0.19.3`)
and `tools/list` returns the action space (`click`/`type_text`/`press_key`/`scroll`/
`get_window_state`/`verify_state`/…). This confirms the plugin.json passthrough command
`{ command: "cua-driver", args: ["mcp"] }` is a working MCP stdio server. Actual
app-driving needs a real display (on a headless box `doctor` warns `DISPLAY`/`WAYLAND_DISPLAY`
unset and window-driving tools fail — as expected).

Note: the release tarball is not a single static binary — it ships `libcua_driver_sdk.so`,
`cua_driver_node_runtime.node`, a cursor theme, and a Wayland helper alongside `cua-driver`.
The official installer lays these out under `~/.cua-driver/`; users should install via the
curl script rather than copying the bare binary.

## Not done / open

- The `command` is `cua-driver` (PATH-relative). Consider documenting the absolute-path
  fallback for non-standard installs in `docs/en/installation.md`.
- Optional: a startup preflight that detects a missing `cua-driver` and prints the install
  hint (currently surfaced only when a tool call fails).
