# MHS-HTTP/1 — the adapter contract

This is the whole contract between the `qwen-mm-plugins-mhs` host and an adapter. If your service
answers these requests, a model can operate your hardware; nothing needs to be added to the plugin.

An adapter is owned and run by whoever owns the hardware. It is a plain HTTP server in any language.
For Python, copy [`adapter_server.py`](adapter_server.py) with [`mock_adapter.py`](mock_adapter.py).
Both use only the standard library. The server handles HTTP/JSON and routing; the example contains
the device implementations that you replace with hardware I/O.

## Python device interface

Create one object per device and pass the objects to
`AdapterServer(("127.0.0.1", 8800), devices)`. Each server owns its registry and device instances;
creating a second server must not share mutable device state through module globals.

| Device method | Responsibility |
|---|---|
| `summary()` | Device ID/type, current state, and capability names for discovery |
| `meta()` | Identity, capabilities, parameter descriptions, and limits |
| `health()` | Fresh health observation; no commands |
| `read(capability, params)` | Validate a read and return observation blocks |
| `write(capability, params)` | Validate the full request before changing state; execute and report feedback |
| `reset(params)` | Optional recovery/stop; accepts `mode: soft` or `mode: estop` |

Raise `MhsError(http_status, code, message)` for an expected device or parameter error. The server
returns the protocol error object; it does not retry writes or invent device cleanup. Devices own
their operation locks, cancellation, hardware resource lifecycle, and stop behavior.

Keep the hardware SDK behind the device object so that contract tests can supply a fake SDK. A
larger adapter can use separate SDK, state, and device modules while keeping exactly this interface.
Metadata and validation must describe the same accepted parameters: reject unknown names, invalid
types, and hard-limit violations rather than silently converting or clamping them. A soft limit
remains advisory, and read overrides must not change persistent settings.

The Python server bounds request bodies to 64 KiB, accepts JSON objects with finite numbers,
supports an optional bearer token, and returns a structured error for unsupported resets. Its
per-connection timeout bounds request reads, not the duration of a device operation. Other adapter
implementations need not use this helper; the HTTP contract below remains unchanged.

## Registering an adapter

The host reads `~/.qwen-mm-plugins/mhs-devices.json` (override the path with `QWEN_MM_MHS_DEVICES`):

```json
{
  "adapters": [
    { "name": "lab-cam", "url": "http://192.168.1.20:8800" },
    { "name": "arm", "url": "https://arm.internal", "timeout": 30,
      "auth": { "type": "bearer", "token_env": "ARM_TOKEN" } }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Short label, unique, no `/`. Device ids are reported to the model as `<name>/<device_id>`. |
| `url` | yes | Base URL, `http` or `https`. The host appends `/mhs/v1/…`. |
| `timeout` | no | Per-request seconds (default 10, max 600). Raise it for slow hardware. |
| `base_path` | no | Overrides `/mhs/v1` when a reverse proxy relocates the API. |
| `auth` | no | `{"type": "bearer", "token_env": "<ENV VAR NAME>"}`. Only the variable *name* goes in this file — never the token itself. |

One adapter may front any number of devices. Requests are sent with proxies explicitly disabled, so
an ambient `HTTP_PROXY` cannot silently intercept traffic meant for the LAN.

## Endpoints

All paths are relative to `<url><base_path>`, i.e. `http://host:8800/mhs/v1`. Every response is a
JSON object. Request bodies are JSON objects; `GET` requests have no body.

| Method | Path | Purpose |
|---|---|---|
| GET | `/devices` | List the devices behind this adapter |
| GET | `/devices/{device_id}` | Full metadata for one device |
| POST | `/devices/{device_id}/read/{capability}` | Read a capability |
| POST | `/devices/{device_id}/write/{capability}` | Command a capability |
| GET | `/devices/{device_id}/health` | Live health |
| POST | `/devices/{device_id}/reset` | Recover or stop — **optional** |

Path segments are URL-encoded by the host, so device ids and capability names may contain spaces.

These paths intentionally match [open-mhs](https://github.com/tongriyaotxt/open-mhs)'s REST server
under an added `/mhs/v1` prefix, so an existing open-mhs deployment is close to a drop-in adapter.

### GET /devices

```json
{
  "devices": [
    { "device_id": "mock-camera",
      "device_type": "camera",
      "state": "online",
      "summary": "Simulated RGB camera.",
      "tags": ["mock", "vision"],
      "capabilities": ["frame", "settings", "temperature"] }
  ]
}
```

Only `device_id` is required; an entry without one is ignored. Include `capabilities` (names only) —
it is what lets the model see what a device can do without a metadata call per device.

### GET /devices/{device_id}

```json
{
  "device_type": "camera",
  "manufacturer": "Acme", "model": "Cam-1", "serial_number": "…", "firmware": "1.4.2",
  "location": "bench", "state": "online", "tags": ["vision"],
  "description": "What this device is and what it is for, in plain language.",
  "documentation_url": "https://…",
  "capabilities": [
    { "name": "frame", "direction": "read",
      "description": "Capture one frame as PNG.",
      "params": { "width": { "type": "integer", "description": "8-512" } } },
    { "name": "settings", "direction": "both",
      "description": "Exposure and gain.",
      "unit": "", "value_type": "object",
      "params": { "exposure": { "type": "integer", "description": "ms, 1-100" } },
      "requires_confirm": false }
  ],
  "safety_limits": [
    { "parameter": "exposure", "min": 1, "max": 100, "unit": "ms", "hard": true,
      "description": "Outside this range the sensor saturates." }
  ]
}
```

Every field except `device_type` is optional, but `description` and `capabilities` are what the model
reasons over — a device with neither is nearly unusable. Missing fields are defaulted, not rejected.

- `direction` — `read`, `write`, or `both`. Anything else is treated as `both`. The host refuses a
  read of a `write` capability and vice versa, before contacting you.
- `params` — free-form per-parameter documentation. The host passes it to the model as-is; it does not
  validate against it, so keep validating in the adapter.
- `requires_confirm: true` — the host refuses this write unless the model explicitly passes
  `confirm=true`. Use it for anything with a physical consequence a person should intend.
- A capability may also be a bare string (`"frame"`), shorthand for direction `both` with no docs.

### Safety limits

`safety_limits` is the part of your metadata with teeth. **The host enforces it before sending the
request**, matching each entry's `parameter` against the write's params by name:

- `hard: true` (also the default when omitted) — the write is refused outright and cannot be
  overridden. Use it for anything that could damage equipment or hurt someone.
- `hard: false` — refused unless the model passes `confirm=true`. Use it for "legal but usually
  wrong".
- `min` / `max` are each optional; omit one for a one-sided bound.

This does not excuse the adapter from validating: host-side checking exists so a bad guess costs a
round trip, not a machine. Enforce your own limits too.

### POST /devices/{device_id}/read/{capability}

Request body is the params object (`{}` when there are none). Respond with result blocks:

```json
{ "blocks": [
    { "type": "value", "name": "temperature", "value": 31.5, "unit": "C" },
    { "type": "text",  "text": "sensor stable" },
    { "type": "image", "data": "<base64>", "mimeType": "image/png" }
] }
```

| Block | Fields | Notes |
|---|---|---|
| `value` | `name`, `value`, `unit?` | A scalar reading. |
| `text` | `text` | Free-form prose. |
| `image` | `data` (base64) **or** `path`, `mimeType?` | Reaches the model as a viewable image. `mimeType` defaults to `image/jpeg`. |

Use `path` only when the adapter shares a filesystem with the host (the localhost case); the host
reads the file itself. An unrecognized block type is passed through as text rather than dropped, so a
future block type degrades instead of losing data.

Responses are capped at 15 MiB, images included. Downscale a frame in the adapter rather than sending
a 40 MP original.

### POST /devices/{device_id}/write/{capability}

Request body is the params object. Respond:

```json
{ "ok": true, "state": "online", "blocks": [ { "type": "text", "text": "applied {'exposure': 40}" } ] }
```

`ok: false` means the device declined the command — the host reports that as a failure even on
HTTP 200, so use it rather than pretending success. `blocks` is optional and follows the read format.

### GET /devices/{device_id}/health

```json
{ "state": "online", "healthy": true, "detail": "sensor responding",
  "checks": [ { "name": "sensor", "ok": true } ] }
```

`state` is conventionally `online`, `busy`, `offline`, `error`, or `maintenance`. This is never cached
by the host — answer it from live state, not from a variable you set at startup.

### POST /devices/{device_id}/reset

Body `{"mode": "soft"}` or `{"mode": "estop"}`. Respond `{"ok": true, "state": "online"}`.

- `soft` — return to idle / known-good, clear an error.
- `estop` — halt motion and output *now*; recovery is secondary.

Reset is **optional**. If the device cannot reset, answer `404`, `405`, or `501` and the host tells the
model plainly that this device cannot be stopped through it. Do not fake success.

## Errors

Use a real HTTP status with an error object:

```json
{ "error": { "code": "unknown_capability", "message": "mock-camera cannot read 'nope'" } }
```

`code` is a short machine token of your choosing; `message` is shown to the model, so write it for a
reader who has to decide what to do next. `401` for a bad bearer token, `404` for an unknown
device/capability, `400` for bad params, `503` when the hardware is unreachable from the adapter.

Always answer. A hang is the worst failure mode — the host can only report a timeout, which tells the
model nothing about whether the command took effect.

## Versioning

`/mhs/v1` is the contract above. New optional fields may be added within `v1`; anything that would
break an existing adapter goes to `/mhs/v2`, and the host will let a registry entry pin `base_path`.

## Checking your adapter

Point the host's conformance checker at it. It exercises every read-only route and tells you precisely
which part of this document you have not met:

```bash
python3 -m qwen_mm_plugins_mhs.verify http://127.0.0.1:8800
```

It validates through the host's own protocol code, so a PASS means the host agrees with you — not that
a second implementation of the rules agrees. Exit status is non-zero when anything failed.

It is **strictly read-only**: it never calls `write` and never calls `reset`, because on real hardware
those move things or stop them. Those two are checked by declaration only, and the report says so. Test
them yourself, deliberately, when it is safe.

## Checklist

- [ ] `python3 -m qwen_mm_plugins_mhs.verify <url>` passes with no ✗ (and you have read the ! lines)
- [ ] `GET /devices` lists every device with `device_id` and `capabilities`
- [ ] `GET /devices/{id}` returns a `description` and per-capability `direction`
- [ ] `safety_limits` declares every range that matters, with `hard` set deliberately
- [ ] `requires_confirm: true` on every write a person should intend
- [ ] reads return typed blocks; frames are downscaled and under 15 MiB
- [ ] writes report `ok: false` when the device declines
- [ ] health is answered from live state
- [ ] reset works, or returns 405 — never a fake success
- [ ] every error path answers with a status and a `message` worth reading
