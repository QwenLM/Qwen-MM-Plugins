# Cookbook — Qwen-MM-Plugins CUA (computer use)

Computer-use for the **local desktop** with `qwen-mm-plugins-cua`: the model launches and drives
**any** native GUI app in the background (whole desktop, not just the browser) — snapshot the
accessibility tree, act on elements / menus / geometry / pixels, and verify from fresh state.

> **Passthrough.** This capability ships no server of its own — it registers **trycua/cua**'s
> **Cua Driver** (MIT) as the MCP server `cua-computer-use`. The external `cua-driver` binary must
> be installed (below), and it only works on a machine with a **real display** (macOS first) — a
> headless/remote server has no screen to drive.

---

## Tools

The MCP server is the external `cua-driver mcp`. Its action space (window / accessibility-tree
first, pixels as fallback):

**Session & apps** — `start_session`, `launch_app`, `list_apps`, `list_windows`
**Perceive** — `get_window_state` (accessibility tree **and** a screenshot together),
`get_desktop_state`, `get_accessibility_tree`, `get_browser_state`
**Act** — `click`, `double_click`, `right_click`, `drag`, `scroll`, `move_cursor`, `type_text`,
`press_key`, `hotkey`, `set_value`, `set_window_frame`, `invoke_menu`
**Verify** — `verify_state` (bounded read-back; `unknown` is not success)
**Browser / recording** — typed page tools, `start_recording` / `replay_trajectory`

Core loop: `start_session` once → `launch_app` → `get_window_state` → act on the fresh
`element_token` → `verify_state`. Prefer element tokens; fall to pixels only when the tree is
degraded. Delivery defaults to `background` (never steals focus).

---

## Install

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude plugin install qwen-mm-plugins-cua@qwen-mm-plugins
```

Then install the **`cua-driver` binary** (native, cross-OS; **not** pulled in by `uvx`):

```bash
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"   # → ~/.local/bin, no admin
```

macOS also needs two permissions (per host app, cannot be granted programmatically):

```bash
open -n -g -a CuaDriver --args serve          # start via the app bundle so grants stick
cua-driver permissions grant                  # Accessibility (drive) + Screen Recording (see)
cua-driver doctor                             # confirms platform + a reachable display
```

> **Don't also run `cua-driver skills install`** — this plugin already vendors that skill (under
> the name `qwen-mm-plugins-cua`); installing the upstream pack too would duplicate it.

> **Headless server**: there is no display to drive — `doctor` will warn `DISPLAY`/`WAYLAND_DISPLAY`
> unset. Run on a local desktop, or target an isolated desktop VM (cua + Lume).

## Notes

- **No API key** — the driving model is whatever your agent harness already uses. `CUA_API_KEY` /
  Lume VMs are only for cua's cloud/sandbox targets. Telemetry is on by default:
  `cua-driver telemetry disable`.
- **Coordinates are pixels, not 0–1000.** `click`/`move_cursor` take raw `x`/`y` with
  `scope: window|desktop`. qwen-mm-plugins-core's `grounding` emits **0–1000 normalized**, so to
  use it for a pixel target, denormalize first: `px = norm / 1000 * (window-or-desktop size)`.
  You can also feed the `get_window_state` screenshot to core `read_image` / `ocr`.
- **Version correspondence**: the vendored skill and the `cua-driver` binary come from the **same**
  cua release (skill `version` == `cua-driver --version`, currently `0.19.3`). Keep them aligned.

---

## Cases

No case recorded yet. Add one in either style — see [core](../core/usage.md) for worked examples:

- **Trace** — a full session rendered to a self-contained HTML page, linked by URL.
- **Result** — the query plus a public link / preview of the produced artifact.

---

## Troubleshooting

- **`cua-computer-use` tools missing / "command not found"**: install `cua-driver` (above) and make
  sure `~/.local/bin` is on `PATH`; or register it directly with `cua-driver mcp-config --client claude`.
- **macOS "not permitted" / blank screenshots**: grant Accessibility + Screen Recording via
  `cua-driver permissions grant`, toggle both on in System Settings, let CuaDriver relaunch.
- **Headless / `DISPLAY` unset**: expected — GUI driving needs a real screen; use a local desktop or a VM.
- **Linux Wayland**: raw background input is limited (BETA); an X11 session is the smoother path.

## Attribution & License

- **cua** is a passthrough to [trycua/cua](https://github.com/trycua/cua)'s Cua Driver (MIT); the
  skill docs under `skill/` are vendored from its skill pack.

Full third-party license is in the capability's `NOTICE.md`.
