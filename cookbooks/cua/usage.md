# Cookbook — Qwen-MM-Plugins CUA (computer use)

Visual-first computer use for a **local desktop** with `qwen-mm-plugins-cua`. The model observes a
native application's current screenshot and Accessibility tree, acts on a fresh element index or
pixel coordinate, and then observes again to verify the result.

> **First-party proxy.** The plugin registers the stable MCP server name
> `qwen-mm-plugins-cua` and forwards stdio MCP to
> [QwenLM/open-computer-use](https://github.com/QwenLM/open-computer-use) (MIT). It needs Node.js
> and a real display; a headless server has no desktop to drive.

---

## Tools and core loop

The runtime deliberately exposes nine tools:

- **Discover:** `list_apps`
- **Observe:** `get_app_state` (screenshot + Accessibility tree)
- **Act:** `click`, `drag`, `type_text`, `press_key`, `scroll`, `set_value`,
  `perform_secondary_action`

Call `get_app_state({app})` at the start of every turn. Use an `element_index` from that state when
the target appears in the tree; otherwise use coordinates from the same screenshot. Treat the state
as stale immediately after any action, and call `get_app_state` again before the next action or
before declaring success.

Pixel clicks and keyboard input may activate the target application. This capability does not
guarantee background delivery.

---

## Install

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude plugin install qwen-mm-plugins-cua@qwen-mm-plugins
```

Install a current Node.js release so `npx` is available. The proxy resolves the runtime in this
order:

1. `QWEN_MM_OPEN_COMPUTER_USE_PATH` — a managed executable path.
2. `npx --yes --package=@qwen-code/open-computer-use@0.2.3 open-computer-use mcp`.
3. `open-computer-use` on `PATH` when `npx` is unavailable.

The npx path downloads the pinned package on first launch. Check resolution with:

```bash
qwen-mm-plugins-cua --check-system
```

On macOS, start the runtime once and approve both Accessibility and Screen Recording when prompted:

```bash
npx --yes --package=@qwen-code/open-computer-use@0.2.3 open-computer-use doctor
```

These permissions are operating-system grants and cannot be enabled programmatically.

---

## Notes

- **No extra API key:** the driving model is the model already used by the agent harness.
- **Fresh coordinates only:** coordinates belong to one returned screenshot and are not durable.
- **Keyboard combinations:** `press_key` accepts xdotool-style strings, for example `super+4` on
  macOS or `ctrl+l` where supported.
- **Irreversible actions:** sending a message, submitting a form, deleting data, or confirming a
  purchase still requires explicit user authorization.

---

## Cases

No case recorded yet. Add one in either style — see [core](../core/usage.md) for worked examples:

- **Trace:** a full session rendered to a self-contained HTML page, linked by URL.
- **Result:** the query plus a public link or preview of the produced artifact.

---

## Troubleshooting

- **Tools missing:** restart the harness and begin a new task after installation; an existing task
  cannot hot-add MCP tools to its inventory.
- **Runtime not found:** install Node.js or set
  `QWEN_MM_OPEN_COMPUTER_USE_PATH=/absolute/path/to/open-computer-use`.
- **macOS “not permitted” or blank screenshots:** grant Accessibility and Screen Recording to the
  runtime process, restart the harness, and retry from a fresh state.
- **Headless or `DISPLAY` unset:** expected; run on a local desktop or a desktop VM.

## Attribution and license

The runtime is [QwenLM/open-computer-use](https://github.com/QwenLM/open-computer-use), licensed
under MIT. See the capability's `NOTICE.md` for attribution.
