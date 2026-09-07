---
name: qwen-mm-plugins-cua
description: Observe and operate desktop applications or exactly bound browser tabs through grounded screenshots, accessibility state, clicks, typing, keys, scrolling, dragging, bounded waits, and typed browser actions.
---

# Computer use

The server exposes one fixed tool profile per process. `native` provides full-desktop visual
control, `ax` provides window-scoped accessibility and visual control, and `full` also provides
typed Browser and limited Runtime tools.

## Screenshot coordinates

Before a screenshot-grounded pointer action, obtain a fresh state from `get_desktop_state`,
`get_app_state`, or the preceding action. Follow its returned `coordinate_space` exactly:

- In `absolute` mode, use pixel coordinates within the encoded PNG bounds.
- In `relative` mode, use the inclusive `[0,1000]` range on both axes.

Ground coordinates against that state's image and metadata, not the dimensions of a resized
preview. Coordinates and `snapshot_id` remain valid only for that state. Browser tools use page
refs or viewport CSS pixels and are unaffected by this coordinate space.

## Native visual loop

Begin with `get_desktop_state`, then alternate one `move_cursor`, `click`, `drag`, `scroll`,
`press_key`, or `type_text` action with inspection of the fresh state returned by that action.
The profile deliberately exposes no app, pid, window, display, coordinate-space, or AX selector.

Keyboard actions go to the frontmost application and `type_text` goes to its focused control. Judge
the result only from the fresh full-screen screenshot returned by the action. Use `wait` for delayed
visual transitions. A snapshot becomes stale after any action or wait; its id binds coordinates to
the image but is not a window identifier.

## AX native-app loop (`ax` and `full`)

Begin with `get_app_state`, then alternate one `click`, `type_text`, `press_key`, `scroll`, `drag`,
or `set_value` action with inspection of the fresh state returned by that action. Use `wait` for
delayed transitions. A snapshot becomes stale after any action or wait.

Platform delivery differs even though the tool contract is shared. Linux requires a graphical
session and AT-SPI; X11 has the broadest coverage, while native Wayland behavior depends on the
compositor. Treat a structured platform refusal as a boundary, not as a successful action.

Prefer a current `element_token` when it truthfully identifies the control. Use
screenshot coordinates for custom-drawn or misleading accessibility surfaces. Element tokens are
valid only with the returned `snapshot_id`.

Enable `retry_if_unverified` only when repeating that specific click is safe. Its final global
pointer fallback can foreground the exact window and move the physical cursor; set
`allow_global_pointer_fallback=false` when that is undesirable. Do not perform irreversible or
externally consequential actions without the user's authorization.

## Typed browser loop (`full`)

Use Browser tools when page DOM semantics or background operation are more reliable than native
window pixels:

1. Use `browser_prepare` only when `get_browser_state` reports that setup is required. Preparation
   must stay within the user's authorized profile strategy.
2. Bind with `get_browser_state(pid, window_id)`, then inspect an exact
   `target_id` + `tab_id` pair.
3. Act with `browser_navigate`, `browser_click`, `browser_type`, `browser_pointer`,
   `browser_dialog`, `browser_set_input_files`, or `browser_download`.
4. Refresh `get_browser_state` and verify the result. Navigation and every newer tab snapshot make
   older page refs stale.

Browser refs, targets, tabs, and sessions are opaque and exact; never synthesize or reuse them
across sessions. If a safe ref action explicitly returns `effect=refused` because trusted input is
unavailable, refresh the page snapshot and retry that action with `input_route=dom_event`; never
treat a refusal as success. `browser_dialog` covers page-owned JavaScript dialogs, not browser
chrome or native file pickers. `browser_set_input_files` requires explicit absolute files.
`browser_download` requires an explicitly approved existing destination directory.

The `full` Runtime tools are intentionally limited to `list_windows`, `get_desktop_state`,
`launch_app`, and `verify_state`. Driver configuration, permissions, recording, session
administration, app termination, clipboard access, and arbitrary raw tool proxying remain hidden.
