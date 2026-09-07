# mhs — operating real hardware

The hardware capability, `qwen-mm-plugins-mhs`. It implements the host side of a
[Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview):
one fixed six-tool surface the model uses to operate any device, with everything device-specific
pushed behind an *adapter*.

The point of the split is that **the adapter belongs to the hardware, not to this plugin**. Whoever
owns a camera writes and runs its adapter; the plugin only needs to know where it is. So the tools the
model sees never change as hardware is added, and this repository contains no driver for anything.

| Tool | What it answers |
|---|---|
| `mhs_discover` | What devices exist, of what type, in what state, with which capabilities |
| `mhs_meta_info` | What one device is, what each capability takes, and what it will refuse |
| `mhs_read` | A sensor value, a current setting, a camera frame |
| `mhs_write` | Send a command — the only tool that changes the physical world |
| `mhs_health_check` | Is it alive, right now (never cached) |
| `mhs_reset` | Return to a known-good state, or stop it immediately |

## Try it without hardware

The capability ships a complete mock adapter — two simulated devices in one stdlib-only file. It lives
beside the Skill, so it is present in a repository checkout and in a plugin install (which copies the
whole capability directory, Skill included). It is *not* part of the pip/uvx wheel, which packages only
the server module.

```bash
# from a checkout
python3 src/capabilities/mhs/skill/references/mock_adapter.py --port 8800
# from a plugin install, under the installed skill directory
python3 <skills>/qwen-mm-plugins-mhs/references/mock_adapter.py --port 8800
```

Register it:

```bash
mkdir -p ~/.qwen-mm-plugins
cat > ~/.qwen-mm-plugins/mhs-devices.json <<'JSON'
{"adapters": [{"name": "mock", "url": "http://127.0.0.1:8800"}]}
JSON
```

Then ask naturally:

```text
What hardware can you see?
Take a picture with the camera and tell me what's in it.
Set the camera exposure to 75 and take another picture. Did it get brighter?
Turn the lamp on.
Set the exposure to 5000.
```

What you should see:

- **"Set the exposure to 5000"** is refused. The mock camera declares a hard limit of 1–100 ms, and the
  host enforces it before the request leaves the process. `confirm=true` cannot override a hard limit.
- **"Turn the lamp on"** is refused once, because the lamp marks `power` as requiring confirmation, and
  succeeds when the model re-issues it with `confirm=true`.
- **Gain above 32 dB** is a *soft* limit: refused, but overridable with `confirm=true`.
- **`mhs_reset` on the lamp** reports that this device does not implement reset — legal in MHS, and
  something the user needs to know, because it means that lamp cannot be stopped through the model.

## Configuration

`~/.qwen-mm-plugins/mhs-devices.json` (override the path with `QWEN_MM_MHS_DEVICES`):

```json
{
  "adapters": [
    { "name": "lab-cam", "url": "http://192.168.1.20:8800" },
    { "name": "arm", "url": "https://arm.internal", "timeout": 30,
      "auth": { "type": "bearer", "token_env": "ARM_TOKEN" } }
  ]
}
```

Only the *name* of the environment variable holding a token goes in this file — never the token.
`QWEN_MM_MHS_CACHE_TTL` (default 60s) bounds how long device lists and metadata are cached; health is
never cached.

Requests are sent with proxies explicitly disabled: hardware is normally on the LAN, and an ambient
`HTTP_PROXY` intercepting those calls looks exactly like broken hardware.

## Installing

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins
claude plugin install qwen-mm-plugins-mhs@qwen-mm-plugins
```

No cloud key and no system tools — the host is a stdlib HTTP client. Pair it with
`qwen-mm-plugins-api` or `qwen-mm-plugins-core` if you want the model to analyze the frames it reads.

## Letting the model write the adapter

Because the registry file is re-read on every call and devices are discovered from adapters at runtime,
an adapter written mid-session works immediately — no restart, no change to this plugin. The Skill tells
the model it may do this when the user has reachable hardware with no adapter for it:

```text
This box has network interfaces but no MHS adapter. Write one and show me eth0's link state.
```

The model reads the protocol spec, writes an adapter, starts it, adds it to the registry, and calls
`mhs_discover`. A worked run against a real host NIC:

```
mhs_discover      → host/eth0, host/lo, host/dummy0   (device_type network_interface)
mhs_read link     → operstate = up · carrier = True · mtu = 1450 bytes · speed = 100000 Mb/s
mhs_read counters → rx_bytes = 2548886186 · tx_packets = 19099118 · rx_errors = 0
mhs_write link    → Error: capability 'link' on host/eth0 is read-only
mhs_reset         → Error: host/eth0 does not implement reset (HTTP 405)
```

Note the last two. The adapter declared every capability `direction: "read"`, so the host refused the
write **locally** — the request never reached the adapter — and reset returned an honest 405 rather than
pretending to bounce a live interface. The Skill instructs the model to start read-only for exactly this
reason: a read-only declaration is host-enforced, so it makes an accidental write unreachable, and it
costs nothing to add later.

An adapter the model wrote has had no review. The Skill requires it to tell you what the adapter talks
to and which capabilities can change physical state before using it on anything that moves.

## Writing an adapter for real hardware

An adapter is a plain HTTP server answering six routes. Any language; no SDK.

1. Read [`adapter_protocol.md`](../../src/capabilities/mhs/skill/references/adapter_protocol.md) —
   the complete contract.
2. Copy [`mock_adapter.py`](../../src/capabilities/mhs/skill/references/mock_adapter.py) and replace
   the `do_read` / `do_write` / `do_health` / `do_reset` bodies with real I/O.
3. Declare `safety_limits` honestly. The host enforces what you declare, so this is the cheapest
   place to make an unsafe command impossible. Keep enforcing them in the adapter too — host-side
   checking exists so a bad guess costs a round trip, not a machine.
4. Mark every write a person should intend with `requires_confirm: true`.
5. Add a line to the registry file. Nothing in this plugin changes.

An existing [open-mhs](https://github.com/tongriyaotxt/open-mhs) REST deployment is close to a
drop-in: the routes match, under an added `/mhs/v1` prefix.

## Safety

These tools move physical things. The Skill instructs the model to read a device's metadata before its
first write, to treat a hard-limit refusal as information rather than an obstacle, to run
`mhs_health_check` before retrying a failed write, and to reach for `mhs_reset mode=estop` first when
something looks wrong. That is guidance, not a guarantee — the enforceable part is what your adapter
declares and what it refuses.
