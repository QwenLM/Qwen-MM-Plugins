# CUA cookbook

`qwen-mm-plugins-cua` exposes a selected visual, accessibility, or full computer-use surface. It
uses the separately installed [Cua Driver](https://github.com/trycua/cua) for capture, accessibility,
typed browser control, Retina/downscale mapping, and input delivery.

## Install Cua Driver

The plugin does not install its runtime. Install Cua Driver 0.20.0 or newer from
the [official guide](https://github.com/trycua/cua/blob/main/docs/content/docs/how-to-guides/driver/install.mdx),
then run it in the same interactive desktop session as the target applications.

### macOS

On macOS 14+, start the signed app service before granting Accessibility and Screen Recording so
the permissions belong to Cua Driver rather than the terminal:

```bash
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
open -n -g -a CuaDriver --args serve
cua-driver permissions grant
cua-driver permissions status
```

Enable CuaDriver in both System Settings lists. Restart the app service after changing a grant.

### Windows

In an interactive Windows 10/11 or Windows Server session, run in PowerShell:

```powershell
irm https://cua.ai/driver/install.ps1 | iex
cua-driver autostart kick
cua-driver autostart status
```

The scheduled task must run in the interactive user session. Session 0 and SSH without an
interactive desktop cannot expose normal GUI windows.

### Linux

Linux requires an x86_64 graphical session and AT-SPI 2. On Debian/Ubuntu:

```bash
sudo apt install libxi6 at-spi2-core
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
cua-driver serve
```

Keep `cua-driver serve` running, or configure the official
[systemd user service](https://github.com/trycua/cua/blob/main/docs/content/docs/how-to-guides/driver/keep-running.mdx).
X11 has the broadest support; native Wayland is compositor-dependent. A headless host needs a
desktop such as Xfce under Xvfb before GUI tools can see windows.

### Verify the runtime and plugin

```bash
cua-driver --version
cua-driver status
cua-driver doctor
qwen-mm-plugins-cua --check-system
```

The adapter resolves `QWEN_MM_CUA_DRIVER_PATH` first, then `PATH`,
`~/.local/bin/cua-driver`, and the standard macOS app-bundle path. It never installs Cua Driver's
optional Skill pack and does not register the driver's complete MCP server.

## Choose a profile

Choose one profile in `~/.qwen-mm-plugins/config`, then restart the MCP server:

```dotenv
QWEN_MM_CUA_TYPE=ax
```

| Profile | Model-visible surface |
|---|---|
| `native` | Primary-display screenshots and absolute-pixel global input; no app/window or accessibility selectors. |
| `ax` | Default. Window-scoped screenshots, OS accessibility, pixel actions, and verification. |
| `full` | `ax` plus typed Browser tools and selected Runtime discovery/verification tools. |

The profile changes the registered MCP schemas, so changing the config requires an MCP reload.

## `native`: primary-display vision

Call `get_desktop_state`, then perform one pointer or keyboard action using its `snapshot_id`.
Read the authoritative `H×W` image metadata emitted immediately before the image block. Pointer
coordinates use that encoded PNG pixel frame and must stay inside it, even if the client or model
pipeline resizes the displayed image. Every action returns a fresh screenshot and makes the previous
snapshot stale.

Pointer actions target the primary display. Keyboard and text actions go to the frontmost
application. Driver 0.20 does not provide arbitrary-display capture here, so the plugin fixes
`display_id=primary` internally.

## `ax`: window accessibility

The `ax` profile provides window-scoped observation and interaction through each platform's
accessibility services:

| Platform | Semantic backend | Practical boundary |
|---|---|---|
| macOS | Accessibility / AX actions | Requires Accessibility and Screen Recording grants. |
| Windows | UI Automation (UIA), with MSAA where applicable | Requires an interactive desktop session. |
| Linux | AT-SPI 2 | X11 is broadest; native Wayland behavior depends on the compositor. |

- Discover and observe with `list_apps` and `get_app_state`.
- Act with `click`, `type_text`, `press_key`, `scroll`, `drag`, or `set_value`.
- Use `wait` for bounded delayed transitions rather than repeating an action.

Pixel coordinates use the absolute pixel frame in the PNG returned by `get_app_state`. Coordinates
and `element_token` values are valid only with the current `snapshot_id`. Every action returns fresh
state.

`delivery=auto` starts in the background. Set `retry_if_unverified=true` only when repeating that
click is safe: retries may foreground the window and the final fallback may move the physical
pointer. Set `allow_global_pointer_fallback=false` to prohibit that final route.

## `full`: browser and runtime

Browser tools include `browser_prepare`, `get_browser_state`, navigation, click/type/pointer,
dialogs, file inputs, and downloads.

Bind a native browser `pid` + `window_id`, then use only the opaque `target_id`, `tab_id`, and page
refs returned for the same session. Refresh `get_browser_state` after each action. Navigation and a
newer snapshot invalidate older refs. Browser actions can remain in the background when the trusted
route supports it. If a safe ref action returns `effect=refused` because trusted input is unavailable,
refresh the snapshot before explicitly retrying with `input_route=dom_event`. Browser control does
not imply permission to attach to or modify a user's existing profile.

Runtime tools are limited to `list_windows`, `get_desktop_state`, `launch_app`, and `verify_state`.
Administrative, recording, clipboard, session-management, app-termination, shell, and raw-proxy
tools remain hidden.
