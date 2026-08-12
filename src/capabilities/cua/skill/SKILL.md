---
name: qwen-mm-plugins-cua
description: Drive a native GUI app (macOS, Windows, Linux) with a small, screenshot-first Computer Use toolset. Use when the user asks to operate a real desktop application; inspect the app state before acting and verify each result from fresh state.
version: 1.0.0
---

# Qwen-MM-Plugins CUA

The runtime is [QwenLM/open-computer-use](https://github.com/QwenLM/open-computer-use). It has a
deliberately small MCP surface: it returns a current screenshot together with an
Accessibility tree, then accepts pixel coordinates or element indexes for actions.

## Core loop

1. Call `list_apps` if the exact app name or bundle ID is unknown.
2. Call `get_app_state({app})` **once at the beginning of every turn**. It starts or resumes the
   app-use session and returns the key-window screenshot plus the Accessibility tree.
3. Use the fresh screenshot's coordinate system for visual actions. Coordinates are not durable;
   never reuse them after an action.
4. Prefer an `element_index` from that same state when an element is unambiguous. Prefer `x`, `y`
   when the task depends on visual content that is absent from the tree.
5. Call `get_app_state({app})` again to confirm the visible result before continuing or declaring
   the task complete.

## The nine tools

| Tool | Use it for |
|---|---|
| `list_apps` | Find running or recently used applications. |
| `get_app_state` | Fresh screenshot + Accessibility tree; required observation step. |
| `click` | Click an `element_index` or screenshot pixel coordinates. |
| `drag` | Drag between screenshot pixel coordinates. |
| `type_text` | Type literal text into an app. |
| `press_key` | Send a key or key combination. |
| `scroll` | Scroll a tree element by pages and direction. |
| `set_value` | Set a writable Accessibility element directly. |
| `perform_secondary_action` | Invoke an element's named secondary Accessibility action. |

## Safety and focus boundary

- Treat a snapshot as stale immediately after any action. Re-observe rather than retrying a click
  on old coordinates.
- Do not send messages, submit forms, confirm purchases, or perform other irreversible actions
  until the user has clearly authorized that result.
- This visual runtime may activate its target application for pixel clicks and keyboard input. Do
  not claim that it can drive an arbitrary app entirely in the background.
- If the user explicitly requires low-focus/background delivery, explain that this capability does
  not guarantee it. Pixel clicks and keyboard input may activate the target application.

## Setup

The runtime is resolved in this order:

1. `QWEN_MM_OPEN_COMPUTER_USE_PATH` — an explicit executable path.
2. `npx --yes --package=@qwen-code/open-computer-use@0.2.3 open-computer-use mcp`.
3. `open-computer-use` on `PATH` when Node.js / npx is unavailable.

The npx fallback needs Node.js and downloads the pinned upstream package on its first launch.
On macOS, grant the runtime Accessibility and Screen Recording permissions if the operating system
asks. Check the runtime with:

```bash
qwen-mm-plugins-cua --check-system
```
