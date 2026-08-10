---
name: qwen-mm-plugins-cua
description: Computer-use (CUA) for the LOCAL desktop — capture the screen and (later) click/type/scroll to drive native apps. Use when the task means operating the machine's GUI directly. Only works when this server runs on a local desktop with a real screen (macOS first); it cannot control a headless/remote server.
---

# CUA — local computer use

Drive the desktop of the machine this server runs on. First cut ships **screenshot**; the
action space (click/type/scroll) is coming.

You have the `qwen-mm-plugins-cua` MCP tools available:

- **screenshot** — capture the local screen as an image and save a full-res PNG. Use it to
  see the current desktop state before acting. Args: `display` (0=all, 1=primary, …),
  `max_dimension` (inline preview downscale), `output_path`.

## When this works — and when it does not

CUA drives the machine **this server runs on**. It needs a real display:

- ✅ Server runs on your **local desktop** (macOS first). Screenshot captures your actual screen.
- ❌ Server runs on a **headless / remote box** (no display). There is nothing to capture — the
  tool returns an error explaining this. Run the server locally instead, or use an isolated
  desktop VM (e.g. trycua/cua + Lume) as the target.

This is the same trade-off every GUI computer-use tool makes (Anthropic Computer Use, OpenAI
Operator, Kimi WebBridge): the agent controls a real screen, either a local machine or a sandbox VM.

## macOS setup (first target)

Screenshot needs **Screen Recording** permission for the process running this server:
System Settings → Privacy & Security → Screen Recording → enable your terminal / uvx, then restart it.
Permission is per host app and cannot be granted programmatically — if a capture returns an error
about permission, walk the user through this. Retina displays report physical pixels; coordinates in
the screenshot text are in that full-resolution space.

## Locating things on screen

To find where to click, pass the saved PNG to **`grounding`** from **`qwen-mm-plugins-core`**
(Qwen-VL returns normalized 0–1000 boxes). Convert those to pixels against the full-res size the
screenshot reports. (Click execution lands with the action-space tools — not shipped yet.)

## Safety

Computer-use lets the agent act on whatever is on screen, driven by content it reads there — treat
it as an untrusted-input surface (prompt-injection risk). Use in controlled tasks; don't run it
against sensitive apps unattended.
