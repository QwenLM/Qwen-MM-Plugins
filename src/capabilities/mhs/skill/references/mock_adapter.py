#!/usr/bin/env python3
"""A complete MHS-HTTP/1 adapter in one stdlib-only file — copy this to front real hardware.

Serves two fake devices so the qwen-mm-plugins-mhs tools can be exercised end to end with no
hardware:

  mock-camera  read frame / read settings / write settings / read temperature, reset
               declares a HARD limit on `exposure` and a SOFT limit on `gain`
  mock-lamp    read power / write power (requires confirmation) / write brightness
               does NOT implement reset, which is legal — reset is optional

Writing `settings` changes what `frame` returns, so the read→write→read loop is visibly real.

Run it:
    python3 mock_adapter.py --port 8800
    python3 mock_adapter.py --port 8800 --token s3cret   # require Authorization: Bearer s3cret

Then point the host at it — ~/.qwen-mm-plugins/mhs-devices.json:
    {"adapters": [{"name": "mock", "url": "http://127.0.0.1:8800"}]}

There are deliberately no third-party imports: an adapter usually runs on the constrained box that
is wired to the hardware, and `python3 mock_adapter.py` should be the whole install step. To adapt it,
replace the bodies of `do_read` / `do_write` / `do_health` / `do_reset` in DEVICES with real I/O and
keep the declared metadata honest — the host enforces the limits you declare here.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BASE_PATH = "/mhs/v1"


# ── a minimal PNG encoder, so "read frame" can return a real image with no imaging library ──
def encode_png(width: int, height: int, rows: list[bytes]) -> bytes:
    """RGB rows (3 bytes per pixel) -> PNG bytes."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolor
    raw = b"".join(b"\x00" + row for row in rows)  # filter byte 0 per scanline
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def synthetic_frame(width: int, height: int, exposure: int, gain: int) -> bytes:
    """A gradient + checkerboard whose brightness tracks the current camera settings."""
    scale = max(0.05, min(2.5, exposure / 40.0)) * (1.0 + gain / 64.0)
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            checker = 40 if (x // 8 + y // 8) % 2 else 0
            r = int(min(255, (x * 255 // max(1, width - 1)) * scale) + checker) % 256
            g = int(min(255, (y * 255 // max(1, height - 1)) * scale)) % 256
            b = int(min(255, 120 * scale)) % 256
            row += bytes((r, g, b))
        rows.append(bytes(row))
    return encode_png(width, height, rows)


class MhsError(Exception):
    """Reply with an MHS error object: {"error": {"code", "message"}} plus an HTTP status."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


# ── device models ──
class MockCamera:
    device_id = "mock-camera"
    device_type = "camera"
    supports_reset = True

    def __init__(self) -> None:
        self.settings = {"exposure": 40, "gain": 0, "width": 96, "height": 64}
        self.state = "online"

    def summary(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "state": self.state,
            "summary": "Simulated RGB camera; frame brightness follows its exposure/gain settings.",
            "tags": ["mock", "vision"],
            "capabilities": ["frame", "settings", "temperature"],
        }

    def meta(self) -> dict[str, Any]:
        return {
            "device_type": self.device_type,
            "manufacturer": "Qwen-MM-Plugins",
            "model": "MockCam-1",
            "serial_number": "MOCK-CAM-0001",
            "firmware": "0.1.0",
            "location": "bench",
            "state": self.state,
            "tags": ["mock", "vision"],
            "description": (
                "A simulated colour camera for exercising MHS without hardware. Capture a frame with "
                "read 'frame'; change exposure or gain with write 'settings' and the next frame changes."
            ),
            "documentation_url": "",
            "capabilities": [
                {
                    "name": "frame",
                    "direction": "read",
                    "description": "Capture one frame and return it as a PNG image.",
                    "params": {
                        "width": {"type": "integer", "description": "Override frame width in pixels (8-512)."},
                        "height": {"type": "integer", "description": "Override frame height in pixels (8-512)."},
                    },
                },
                {
                    "name": "settings",
                    "direction": "both",
                    "description": "Current exposure/gain/resolution; write any subset to change them.",
                    "params": {
                        "exposure": {"type": "integer", "description": "Exposure in ms (1-100)."},
                        "gain": {"type": "integer", "description": "Analog gain in dB (0-32)."},
                    },
                },
                {
                    "name": "temperature",
                    "direction": "read",
                    "description": "Sensor temperature.",
                    "unit": "C",
                    "value_type": "number",
                },
            ],
            "safety_limits": [
                {
                    "parameter": "exposure",
                    "min": 1,
                    "max": 100,
                    "unit": "ms",
                    "hard": True,
                    "description": "Outside this range the sensor saturates or stalls the pipeline.",
                },
                {
                    "parameter": "gain",
                    "min": 0,
                    "max": 32,
                    "unit": "dB",
                    "hard": False,
                    "description": "Above 32 dB the image is dominated by noise, but it will not damage anything.",
                },
            ],
        }

    def do_read(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability == "frame":
            width = _clamp_int(params.get("width", self.settings["width"]), 8, 512)
            height = _clamp_int(params.get("height", self.settings["height"]), 8, 512)
            png = synthetic_frame(width, height, self.settings["exposure"], self.settings["gain"])
            return {
                "blocks": [
                    {
                        "type": "image",
                        "data": base64.b64encode(png).decode("ascii"),
                        "mimeType": "image/png",
                    },
                    {
                        "type": "text",
                        "text": f"{width}x{height} frame at exposure={self.settings['exposure']}ms "
                        f"gain={self.settings['gain']}dB",
                    },
                ]
            }
        if capability == "settings":
            return {
                "blocks": [
                    {"type": "value", "name": name, "value": value} for name, value in sorted(self.settings.items())
                ]
            }
        if capability == "temperature":
            reading = 31.5 + self.settings["gain"] * 0.25
            return {"blocks": [{"type": "value", "name": "temperature", "value": round(reading, 2), "unit": "C"}]}
        raise MhsError(404, "unknown_capability", f"{self.device_id} cannot read {capability!r}")

    def do_write(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability != "settings":
            raise MhsError(404, "unknown_capability", f"{self.device_id} cannot write {capability!r}")
        changed = {}
        for name in ("exposure", "gain", "width", "height"):
            if name in params:
                self.settings[name] = _clamp_int(params[name], 0, 4096)
                changed[name] = self.settings[name]
        if not changed:
            raise MhsError(400, "no_parameters", "write 'settings' needs at least one of exposure/gain/width/height")
        return {
            "ok": True,
            "state": self.state,
            "blocks": [{"type": "text", "text": f"applied {changed}"}],
        }

    def do_health(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "healthy": self.state == "online",
            "detail": "simulated sensor responding",
            "checks": [
                {"name": "sensor", "ok": True},
                {"name": "link", "ok": True},
            ],
        }

    def do_reset(self, mode: str) -> dict[str, Any]:
        self.settings.update({"exposure": 40, "gain": 0})
        self.state = "online"
        return {"ok": True, "state": self.state}


class MockLamp:
    device_id = "mock-lamp"
    device_type = "smart_light"
    supports_reset = False  # legal: reset is optional in MHS

    def __init__(self) -> None:
        self.on = False
        self.brightness = 50
        self.state = "online"

    def summary(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "state": self.state,
            "summary": "Simulated lamp; switching it on requires confirmation.",
            "tags": ["mock"],
            "capabilities": ["power", "brightness"],
        }

    def meta(self) -> dict[str, Any]:
        return {
            "device_type": self.device_type,
            "manufacturer": "Qwen-MM-Plugins",
            "model": "MockLamp-1",
            "serial_number": "MOCK-LAMP-0001",
            "firmware": "0.1.0",
            "location": "bench",
            "state": self.state,
            "tags": ["mock"],
            "description": "A simulated lamp, used to demonstrate a write that requires confirmation.",
            "documentation_url": "",
            "capabilities": [
                {
                    "name": "power",
                    "direction": "both",
                    "description": "Switch the lamp on or off.",
                    "value_type": "boolean",
                    "params": {"on": {"type": "boolean", "description": "True to switch on."}},
                    "requires_confirm": True,
                },
                {
                    "name": "brightness",
                    "direction": "both",
                    "description": "Brightness percentage.",
                    "unit": "%",
                    "params": {"level": {"type": "integer", "description": "0-100."}},
                },
            ],
            "safety_limits": [
                {"parameter": "level", "min": 0, "max": 100, "unit": "%", "hard": True},
            ],
        }

    def do_read(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability == "power":
            return {"blocks": [{"type": "value", "name": "on", "value": self.on}]}
        if capability == "brightness":
            return {"blocks": [{"type": "value", "name": "level", "value": self.brightness, "unit": "%"}]}
        raise MhsError(404, "unknown_capability", f"{self.device_id} cannot read {capability!r}")

    def do_write(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability == "power":
            self.on = bool(params.get("on"))
            return {"ok": True, "state": self.state, "blocks": [{"type": "text", "text": f"lamp on={self.on}"}]}
        if capability == "brightness":
            self.brightness = _clamp_int(params.get("level", self.brightness), 0, 100)
            return {
                "ok": True,
                "state": self.state,
                "blocks": [{"type": "value", "name": "level", "value": self.brightness, "unit": "%"}],
            }
        raise MhsError(404, "unknown_capability", f"{self.device_id} cannot write {capability!r}")

    def do_health(self) -> dict[str, Any]:
        return {"state": self.state, "healthy": True, "detail": "simulated lamp responding", "checks": []}

    def do_reset(self, mode: str) -> dict[str, Any]:
        raise MhsError(405, "reset_unsupported", f"{self.device_id} does not implement reset")


def _clamp_int(value: object, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise MhsError(400, "bad_parameter", f"expected an integer, got {value!r}") from None
    return max(low, min(high, number))


DEVICES: dict[str, Any] = {d.device_id: d for d in (MockCamera(), MockLamp())}


# ── HTTP plumbing ──
class Handler(BaseHTTPRequestHandler):
    server_version = "mhs-mock-adapter/1"
    token: str | None = None
    verbose: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- request routing --
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's required name
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            self._check_auth()
            self._send(200, self._route(method, self._segments(), self._body()))
        except MhsError as exc:
            self._send(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:  # noqa: BLE001 — an adapter must answer, not hang up
            self._send(500, {"error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}})

    def _segments(self) -> list[str]:
        path = self.path.split("?", 1)[0]
        if not path.startswith(BASE_PATH + "/"):
            raise MhsError(404, "not_found", f"unknown path {path!r}; this adapter serves {BASE_PATH}/…")
        from urllib.parse import unquote

        return [unquote(s) for s in path[len(BASE_PATH) + 1 :].split("/") if s]

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MhsError(400, "bad_json", f"request body is not JSON: {exc}") from None
        if not isinstance(payload, dict):
            raise MhsError(400, "bad_json", "request body must be a JSON object")
        return payload

    def _check_auth(self) -> None:
        if not self.token:
            return
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            raise MhsError(401, "unauthorized", "missing or wrong bearer token")

    def _route(self, method: str, seg: list[str], body: dict[str, Any]) -> dict[str, Any]:
        if seg == ["devices"] and method == "GET":
            return {"devices": [d.summary() for d in DEVICES.values()]}
        if not seg or seg[0] != "devices" or len(seg) < 2:
            raise MhsError(404, "not_found", f"unknown path {self.path!r}")

        device = DEVICES.get(seg[1])
        if device is None:
            known = ", ".join(DEVICES) or "none"
            raise MhsError(404, "unknown_device", f"no device {seg[1]!r}; this adapter has: {known}")
        rest = seg[2:]

        if not rest and method == "GET":
            return device.meta()
        if rest == ["health"] and method == "GET":
            return device.do_health()
        if rest == ["reset"] and method == "POST":
            if not device.supports_reset:
                raise MhsError(405, "reset_unsupported", f"{device.device_id} does not implement reset")
            return device.do_reset(body.get("mode", "soft"))
        if len(rest) == 2 and rest[0] == "read" and method == "POST":
            return device.do_read(rest[1], body)
        if len(rest) == 2 and rest[0] == "write" and method == "POST":
            return device.do_write(rest[1], body)
        raise MhsError(404, "not_found", f"unknown path {self.path!r} for {method}")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_server(host: str = "127.0.0.1", port: int = 0, token: str | None = None, verbose: bool = False):
    """Create (but do not serve) a mock adapter. port=0 picks a free one — handy in tests."""
    handler = type("BoundHandler", (Handler,), {"token": token, "verbose": verbose})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mock MHS-HTTP/1 adapter (two simulated devices)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8800, help="bind port (default 8800; 0 picks a free one)")
    parser.add_argument("--token", default=None, help="require this bearer token on every request")
    parser.add_argument("--verbose", action="store_true", help="log each request to stderr")
    args = parser.parse_args(argv)

    server = build_server(args.host, args.port, args.token, args.verbose)
    host, port = server.server_address[:2]
    print(f"mock MHS adapter on http://{host}:{port}{BASE_PATH}  devices: {', '.join(DEVICES)}", flush=True)
    print(f'register it with: {{"adapters": [{{"name": "mock", "url": "http://{host}:{port}"}}]}}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
