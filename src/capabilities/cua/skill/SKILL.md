---
name: qwen-mm-plugins-cua
description: Computer-use for the LOCAL desktop — drive any native GUI app (macOS, Windows, Linux) in the background: snapshot its accessibility tree, act via snapshot-bound element tokens, native menu paths, exact window geometry, or pixel coordinates, then verify from fresh state. Passthrough to trycua/cua's Cua Driver — the external `cua-driver` binary must be installed. Use when the task means operating a real app's GUI on a machine with a real screen (headless/remote servers can't be driven).
---

# CUA — local computer use (Cua Driver passthrough)

This capability implements nothing itself. It registers **Cua Driver** (open-source
[trycua/cua](https://github.com/trycua/cua), MIT) as the MCP server `cua-computer-use`,
giving the agent **whole-desktop** computer-use — drive **any** native GUI app in the
background (not just the browser).

## Prefer the official skill for the full playbook

Cua Driver ships its own detailed, multi-file Agent Skill (`SKILL.md` + `MACOS.md` /
`WINDOWS.md` / `LINUX.md` / `BROWSER.md`). It is the **authoritative** guide and is turnkey:

```bash
cua-driver skills install      # fetch the official pack + link it into this agent
cua-driver skills status       # what's installed / linked
```

When that pack is present, follow it. This file is a thin router covering only what to do
**before** it is loaded: install the driver, and the handful of invariants below so behaviour
is safe even without the full pack.

## Prerequisite: install Cua Driver (once)

`cua-driver` is a native cross-OS binary installed by cua's own script — not bundled with
Qwen-MM-Plugins, not pulled in by `uvx`. If the `cua-computer-use` tools are missing or error
with "command not found":

```bash
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"   # → ~/.local/bin, no admin
# macOS: start via the app bundle so permission grants stick to CuaDriver.app
open -n -g -a CuaDriver --args serve
cua-driver permissions grant     # Accessibility (to drive) + Screen Recording (to see)
cua-driver doctor                # confirms platform + that a display is reachable
```

No extra API key — the driving model is whatever your agent harness already uses. Telemetry
is on by default: `cua-driver telemetry disable` to stop it.

## Core invariants (hold these even without the official pack)

- **Snapshot before, verify after — every action.** `get_window_state(pid, window_id)` returns
  the accessibility tree *and* a screenshot together; act, then `verify_state(..., expect=[…])`.
  A tool call returning success is *not* task success — `unknown` is not success.
- **Narrowest semantic route first, then escalate only on a real signal:**
  headless API / CLI / filesystem (non-GUI outcomes) → typed Cua op (`set_window_frame` for
  geometry, `invoke_menu` for menu commands, browser tools for pages) → **AX element action**
  (`element_token`) → **pixel action** off the same screenshot → `foreground` delivery →
  desktop fallback. Don't jump to pixels when an element token works.
- **Never steal focus.** Delivery defaults to `background` (drives without raising/activating
  the window). Only escalate to `foreground` when evidence says the background action didn't
  land. On macOS, do **not** use `open` / `osascript … activate` / `cliclick` — use
  `launch_app`, `click`, `type_text`, `hotkey` instead (see the official `MACOS.md`).
- **Coordinates are pixels, not normalized.** `click`/`move_cursor` take `x`/`y` as raw pixels
  with `scope: "window" | "desktop"` — **not** a 0–1000 space. Prefer `element_token` from
  `get_window_state`; only fall to pixels when the tree is degraded/wrong vs the screenshot.
  (So core's `grounding`, which emits 0–1000, is largely unnecessary here; if you do use it for
  the pixel path, denormalize `px = norm/1000 * window-or-desktop size` first.)

## When this works — and when it does not

Cua Driver drives the machine **this server runs on**, so it needs a real display:

- ✅ Agent + server on your **local desktop** (macOS first) → drives your actual apps.
- ❌ **Headless / remote** box → nothing to drive (`doctor` warns `DISPLAY`/`WAYLAND_DISPLAY`
  unset). Run locally, or target an isolated desktop VM (cua + Lume).

Same trade-off as every GUI computer-use tool (Anthropic Computer Use, OpenAI Operator, Kimi
WebBridge): control a real screen — local machine or sandbox VM.

## Safety

Computer-use acts on whatever is on screen, driven by content it reads there — an untrusted
input surface (prompt-injection risk). Use in controlled tasks; don't run unattended against
sensitive apps. For real workloads prefer the driver's `bounded` permission mode over
`standard` (see the cua docs).
