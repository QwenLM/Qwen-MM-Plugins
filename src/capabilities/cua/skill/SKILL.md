---
name: qwen-mm-plugins-cua
description: Computer-use (CUA) for the LOCAL desktop — launch apps and click/type/scroll native GUI apps in the background, via trycua/cua's Cua Driver. Use when a task means operating the machine's desktop directly. This is a passthrough to the external `cua-driver` binary: it must be installed first, and only works on a local desktop with a real screen (macOS first), not a headless/remote server.
---

# CUA — local computer use (Cua Driver passthrough)

This capability does not implement computer-use itself. It registers **Cua Driver**
(from open-source [trycua/cua](https://github.com/trycua/cua), MIT) as the MCP server
`cua-computer-use`, so your agent can drive native desktop apps in the background.

The driver exposes a window/accessibility-tree action space (not raw pixel clicks), e.g.:
`launch_app`, `get_window_state`, `click [element_index=…]`, `type`, `scroll`. The agent
takes a window snapshot, references elements by index, and acts — more reliable than
pixel-coordinate clicking.

## Prerequisite: install Cua Driver (once)

The `cua-driver` binary is a native cross-OS tool installed by cua's own script — it is
**not** bundled with Qwen-MM-Plugins and does not ride the `uvx` install. If the
`cua-computer-use` tools are missing or error with "command not found", install it:

```bash
# 1. Install the driver (macOS 14+, Windows, or Linux; no admin needed)
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"

# 2. macOS: start the daemon via the app bundle so permission grants stick to CuaDriver.app
open -n -g -a CuaDriver --args serve
cua-driver permissions grant          # grants Accessibility + Screen Recording
cua-driver permissions status         # verify both landed (rerun grant if one is missing)

# 3. Sanity check: the driver can see your desktop
cua-driver doctor
cua-driver call list_apps
```

No extra API key is needed — the driving model is whatever your agent harness already
uses. `CUA_API_KEY` and Lume VMs are only for cua's cloud/sandbox targets, not this
local-driver path.

## When this works — and when it does not

Cua Driver drives the machine **this server runs on**, so it needs a real display:

- ✅ Agent + server run on your **local desktop** (macOS first) → drives your actual apps.
- ❌ **Headless / remote** box (no display) → nothing to drive. Run locally, or target an
  isolated desktop VM (cua + Lume) instead.

This is the trade-off every GUI computer-use tool makes (Anthropic Computer Use, OpenAI
Operator, Kimi WebBridge): control a real screen — local machine or sandbox VM.

## macOS notes

Two separate permissions are required and cannot be granted programmatically:
**Accessibility** (to drive) and **Screen & System Audio Recording** (to see). macOS lists
CuaDriver with the toggle **off** after the prompt — the user must flip it on, then let
macOS relaunch the driver so it picks up the grant. If a tool errors about permission,
walk the user through `cua-driver permissions grant` + toggling both in System Settings.

## Safety

Computer-use lets the agent act on whatever is on screen, driven by content it reads there
— treat it as an untrusted-input surface (prompt-injection risk). Use in controlled tasks;
don't run it unattended against sensitive apps. For real workloads consider the driver's
`bounded` permission mode (see cua docs) rather than `standard`.
