---
name: qwen-mm-plugins-mhs
description: Operate physical hardware (cameras, sensors, lamps, arms, lab equipment) through Model Hardware Standard adapters — mhs_discover (what devices exist), mhs_meta_info (what one device can do and its safety limits), mhs_read (sensor values, camera frames), mhs_write (send a command), mhs_health_check (is it alive), mhs_reset (recover or emergency-stop). Use whenever a task involves reading from or controlling a real device rather than a file.
---

# Qwen-MM-Plugins MHS

You have `qwen-mm-plugins-mhs` MCP tools available. They operate **real hardware**. MHS is to hardware
what MCP is to tools: one fixed six-tool surface, with everything device-specific behind an *adapter*
that the hardware's owner runs. You never learn a per-device API — you ask the device what it can do.

The tool surface does not grow when new hardware appears. A new device shows up in `mhs_discover`
because someone started an adapter for it.

Check the `qwen-mm-plugins-mhs` tools in your tool list for full schemas.

## The loop

1. **`mhs_discover`** — always first. Device ids come from here, never from a guess. Ids look like
   `<adapter>/<device_id>`; a bare id works when only one adapter has it.
2. **`mhs_meta_info`** — before your first `mhs_write` to a device. This is where you learn the
   capability's parameters, units, permitted ranges, and which writes need confirmation. Skipping it
   means guessing at values that move physical things.
3. **`mhs_read`** — sensor values, current settings, camera frames. A frame comes back as an image you
   can look at directly. Read-only; safe to repeat.
4. **`mhs_write`** — the only tool that changes the world. See below.
5. **`mhs_health_check`** — after anything unexpected, and before a sequence of commands. Never cached.
   Call it with no `device_id` to survey everything when you don't yet know what's wrong.
6. **`mhs_reset`** — `mode="soft"` clears an error and returns the device to idle;
   `mode="estop"` stops it now.

## Writing to hardware

- **Read the metadata first.** Ranges and units are declared per device; nothing is universal.
- **A refusal is information, not an obstacle.** If a write is refused for exceeding a *hard* safety
  limit, the device has said that value is unsafe. Do not look for a way around it — report it.
  `confirm=true` cannot override a hard limit and trying is not a plan.
- **`confirm=true` is for deliberate acts,** not for retrying. Use it when metadata says a capability
  requires confirmation, or to exceed a *soft* limit you have a specific reason to exceed. If a human
  hasn't asked for the consequence, don't confirm it.
- **A failed write is not automatically retryable.** Call `mhs_health_check` first. Repeating a command
  at a device in an error state is how a stuck actuator becomes a broken one.
- **Say what you actually did.** Report the device id, capability, and values you sent, and what the
  device answered — not just "done".

## When something is wrong

If a device behaves unexpectedly, or you are unsure whether a command took effect, `mhs_reset` with
`mode="estop"` first and diagnose second. It needs no confirmation, because anything standing between
you and a stop is a hazard. Then tell the user what you stopped and why.

If a device does not implement reset, the tool says so — that device cannot be stopped through you,
and the user needs to know that immediately.

## Configuration

Adapters are listed in `~/.qwen-mm-plugins/mhs-devices.json` (override with `QWEN_MM_MHS_DEVICES`):

```json
{"adapters": [{"name": "lab-cam", "url": "http://192.168.1.20:8800"}]}
```

If that file is missing, the tools say so and show the shape to create. `QWEN_MM_MHS_CACHE_TTL`
(default 60s) controls how long device lists and metadata are cached; health is never cached.

No hardware to hand? Run the bundled mock adapter and point the registry at it:

```bash
python3 references/mock_adapter.py --port 8800
```

It serves a simulated camera (with a hard limit on exposure and a soft limit on gain) and a lamp whose
`power` write requires confirmation — enough to exercise the whole loop.

## Adding hardware

Nothing about a new device belongs in this plugin. An adapter is a plain HTTP server — any language —
and it is registered by one line in the registry file. The full contract is
[`references/adapter_protocol.md`](references/adapter_protocol.md), and
[`references/mock_adapter.py`](references/mock_adapter.py) is a working one to copy.

### You can write the adapter yourself

If the user has hardware you can reach — a device file, a `/sys` node, a serial port, a gRPC or HTTP
service, a vendor CLI — but no MHS adapter for it, write one and use it in the same session. Nothing
needs restarting: the registry file is re-read on every call, and devices are discovered from adapters
at runtime.

1. Read `references/adapter_protocol.md`. Do not guess the shapes from these tool descriptions.
2. Copy `references/mock_adapter.py` and replace the read/write bodies with real I/O for the device.
   Keep it outside this plugin's directory — an adapter belongs with its hardware.
3. Run it in the background on a free port, and check it started before going further.
4. Add it to `~/.qwen-mm-plugins/mhs-devices.json` (create the file if absent, and **preserve any
   adapters already listed** — do not overwrite someone else's registry).
5. `mhs_discover` — the device is there. Then proceed with the normal loop.

Rules for an adapter you wrote yourself, because you are now on both sides of the safety boundary:

- **Start read-only.** Declare `direction: "read"` for every capability until the user has confirmed
  they want the device actuated. A read-only declaration is enforced by the host, so it costs nothing
  and it means an accidental `mhs_write` cannot reach the hardware at all.
- **Declare real `safety_limits`.** The host enforces exactly what you declare, so this is the cheapest
  place to make an unsafe command impossible. Do not declare a range wider than you can justify from
  the device's documentation.
- **Mark consequential writes `requires_confirm: true`.**
- **Never fake success.** Return `ok: false` when the device declines and a 405 when it cannot reset.
  You will be the one misled later.
- **Tell the user what you built before you use it to move anything** — what device it talks to, which
  capabilities it exposes, and which of them can change physical state. An adapter you wrote has had no
  review; say so.

## Relationship to other capabilities

- A camera frame from `mhs_read` is an ordinary image: analyze it with `qwen-mm-plugins-api`
  (`vision_chat`, `ocr`, `grounding`) or annotate it with `qwen-mm-plugins-core` (`crop`, `draw_bbox`).
- On a text-only host (`QWEN_MM_NATIVE_MODE=0`) frames arrive as generated captions instead of images;
  the loop is unchanged.
- This capability reads no files and calls no cloud API — it only talks to the configured adapters.
