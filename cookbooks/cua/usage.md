# CUA cookbook

`qwen-mm-plugins-cua` exposes a selected visual, accessibility, or full computer-use surface. It
uses the separately installed [Cua Driver](https://github.com/trycua/cua) for capture, accessibility,
typed browser control, Retina/downscale mapping, and input delivery.

## Install Cua Driver

The plugin does not install its runtime. For the current integration build, install the matching
Driver source branch in the same interactive desktop session as the target applications.

### macOS and Linux

```bash
git clone --branch qwen-mm-plugins --single-branch https://github.com/JJJYmmm/cua.git
cd cua
bash libs/cua-driver/scripts/install-local.sh --release --autostart
~/.local/bin/cua-driver-local --version
```

On macOS this installs `/Applications/CuaDriverLocal.app` and
`~/.local/bin/cua-driver-local`. Grant Accessibility and Screen Recording to the local app:

```bash
~/.local/bin/cua-driver-local permissions grant
~/.local/bin/cua-driver-local permissions status
```

Rebuilding an ad-hoc-signed local app may require granting those permissions again.

Linux requires a graphical session and AT-SPI 2. On Debian/Ubuntu, install the platform packages
before building:

```bash
sudo apt install libxi6 at-spi2-core
```

The `--autostart` option registers a LaunchAgent on macOS or a systemd user service on Linux.
X11 has the broadest Linux support; native Wayland behavior depends on the compositor. A headless
host needs a desktop such as Xfce under Xvfb before GUI tools can see windows.

### Windows

In an interactive Windows 10/11 or Windows Server session, run in PowerShell:

```powershell
git clone --branch qwen-mm-plugins --single-branch https://github.com/JJJYmmm/cua.git
cd cua
.\libs\cua-driver\scripts\install-local.ps1
cua-driver-local --version
```

The scheduled task must run in the interactive user session. Session 0 and SSH without an
interactive desktop cannot expose normal GUI windows.

### Configure the plugin

The source installer creates `cua-driver-local` without replacing a published `cua-driver`. Resolve
its absolute path with `command -v cua-driver-local` on macOS/Linux or
`(Get-Command cua-driver-local).Source` on Windows, then save that path in
`~/.qwen-mm-plugins/config`:

```dotenv
QWEN_MM_CUA_DRIVER_PATH=/absolute/path/to/cua-driver-local
```

Reload the MCP server after changing the config. Published Driver releases remain available from
the [official installation guide](https://github.com/trycua/cua/blob/main/docs/content/docs/how-to-guides/driver/install.mdx).
When a release contains the required integration changes, it can be used instead by pointing
`QWEN_MM_CUA_DRIVER_PATH` at its `cua-driver` executable.

### Verify the runtime and plugin

```bash
cua-driver-local --version
cua-driver-local status
cua-driver-local doctor
qwen-mm-plugins-cua --check-system
```

The adapter resolves `QWEN_MM_CUA_DRIVER_PATH` first, then `PATH`,
`~/.local/bin/cua-driver`, and the standard macOS app-bundle path. It never installs Cua Driver's
optional Skill pack and does not register the driver's complete MCP server.

## Choose a profile

Choose one profile in `~/.qwen-mm-plugins/config`:

```dotenv
QWEN_MM_CUA_TYPE=ax
QWEN_MM_CUA_COORDINATE_MODE=absolute
```

| Profile | Model-visible surface |
|---|---|
| `native` | Primary-display screenshots and global input; no app/window or accessibility selectors. |
| `ax` | Default. Window-scoped screenshots, OS accessibility, screenshot actions, and verification. |
| `full` | `ax` plus typed Browser tools and selected Runtime discovery/verification tools. |

These settings are read when the plugin process starts, and the profile determines which tools are
registered. To apply a change to an existing client, use its plugin/MCP reload action or start a new
session; there is no separately managed MCP service to restart.

`QWEN_MM_CUA_COORDINATE_MODE` separately selects the screenshot coordinate convention used by
Native and AX tools:

| Mode | Coordinate contract |
|---|---|
| `absolute` | Default. `x,y` are pixels in the encoded PNG dimensions returned with the snapshot. |
| `relative` | Both axes use the normalized inclusive range `[0,1000]`; the server maps it to the snapshot PNG. |

Each observation advertises the active `coordinate_space`; follow that response rather than assuming
a mode. Coordinate-mode changes apply on the next plugin process in the same way. Browser points and
scroll deltas remain viewport CSS pixels, while DOM/page refs are preferred.

## `native`: primary-display vision

Call `get_desktop_state`, then perform one pointer or keyboard action using its `snapshot_id`.
Read the authoritative coordinate instruction emitted immediately before the image block. In
absolute mode, pointer coordinates use the encoded PNG pixel frame. In relative mode, use the
returned `[0,1000]` frame. Every action returns a fresh screenshot and makes the previous snapshot
stale.

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

Screenshot coordinates use the `coordinate_space` returned by `get_app_state`. Coordinates and
`element_token` values are valid only with the current `snapshot_id`. Every action returns fresh state.

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
