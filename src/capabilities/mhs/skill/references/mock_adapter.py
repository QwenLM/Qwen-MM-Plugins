#!/usr/bin/env python3
"""Two simulated devices on the shared stdlib MHS-HTTP/1 server.

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
copy this file with adapter_server.py, then replace read/write/health/reset with real I/O.
Validate parameters at the device boundary and keep metadata consistent with those checks.
"""

from __future__ import annotations

import argparse
import base64
import struct
import threading
import zlib
from typing import Any

from adapter_server import BASE_PATH, AdapterServer, MhsError

CAMERA_DEFAULTS = {"exposure": 40, "gain": 0, "width": 96, "height": 64}
CAMERA_RANGES = {"exposure": (1, 100), "gain": (0, 4096), "width": (8, 512), "height": (8, 512)}


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


# ── device models ──
class MockCamera:
    device_id = "mock-camera"
    device_type = "camera"

    def __init__(self) -> None:
        self.settings = dict(CAMERA_DEFAULTS)
        self.state = "online"
        self.lock = threading.RLock()

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
                        "gain": {"type": "integer", "description": "Analog gain in dB (0-4096); recommended 0-32."},
                        "width": {"type": "integer", "description": "Frame width in pixels (8-512)."},
                        "height": {"type": "integer", "description": "Frame height in pixels (8-512)."},
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
                    "min": CAMERA_RANGES["exposure"][0],
                    "max": CAMERA_RANGES["exposure"][1],
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
                *[
                    {"parameter": name, "min": low, "max": high, "unit": "px", "hard": True}
                    for name in ("width", "height")
                    for low, high in [CAMERA_RANGES[name]]
                ],
            ],
        }

    def read(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability == "frame":
            _only(params, {"width", "height"})
            with self.lock:
                settings = dict(self.settings)
            width = _integer("width", params.get("width", settings["width"]), *CAMERA_RANGES["width"])
            height = _integer("height", params.get("height", settings["height"]), *CAMERA_RANGES["height"])
            png = synthetic_frame(width, height, settings["exposure"], settings["gain"])
            return {
                "blocks": [
                    {
                        "type": "image",
                        "data": base64.b64encode(png).decode("ascii"),
                        "mimeType": "image/png",
                    },
                    {
                        "type": "text",
                        "text": f"{width}x{height} frame at exposure={settings['exposure']}ms "
                        f"gain={settings['gain']}dB",
                    },
                ]
            }
        if capability == "settings":
            _only(params, set())
            with self.lock:
                settings = dict(self.settings)
            return {
                "blocks": [{"type": "value", "name": name, "value": value} for name, value in sorted(settings.items())]
            }
        if capability == "temperature":
            _only(params, set())
            with self.lock:
                reading = 31.5 + self.settings["gain"] * 0.25
            return {"blocks": [{"type": "value", "name": "temperature", "value": round(reading, 2), "unit": "C"}]}
        raise MhsError(404, "unknown_capability", f"{self.device_id} cannot read {capability!r}")

    def write(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability != "settings":
            raise MhsError(404, "unknown_capability", f"{self.device_id} cannot write {capability!r}")
        _only(params, set(CAMERA_RANGES))
        if not params:
            raise MhsError(400, "no_parameters", "write 'settings' needs at least one of exposure/gain/width/height")
        # Validate the whole request before changing any setting.
        changed = {name: _integer(name, value, *CAMERA_RANGES[name]) for name, value in params.items()}
        with self.lock:
            self.settings.update(changed)
        return {
            "ok": True,
            "state": self.state,
            "blocks": [{"type": "text", "text": f"applied {changed}"}],
        }

    def health(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "healthy": self.state == "online",
            "detail": "simulated sensor responding",
            "checks": [
                {"name": "sensor", "ok": True},
                {"name": "link", "ok": True},
            ],
        }

    def reset(self, params: dict[str, Any]) -> dict[str, Any]:
        _only(params, {"mode"})
        if params.get("mode", "soft") not in ("soft", "estop"):
            raise MhsError(400, "bad_parameter", "reset mode must be soft or estop")
        with self.lock:
            self.settings = dict(CAMERA_DEFAULTS)
            self.state = "online"
        return {"ok": True, "state": self.state}


class MockLamp:
    device_id = "mock-lamp"
    device_type = "smart_light"

    def __init__(self) -> None:
        self.on = False
        self.brightness = 50
        self.state = "online"
        self.lock = threading.RLock()

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

    def read(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        _only(params, set())
        if capability == "power":
            with self.lock:
                return {"blocks": [{"type": "value", "name": "on", "value": self.on}]}
        if capability == "brightness":
            with self.lock:
                return {"blocks": [{"type": "value", "name": "level", "value": self.brightness, "unit": "%"}]}
        raise MhsError(404, "unknown_capability", f"{self.device_id} cannot read {capability!r}")

    def write(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability == "power":
            _only(params, {"on"})
            if not isinstance(params.get("on"), bool):
                raise MhsError(400, "bad_parameter", "on must be a boolean")
            with self.lock:
                self.on = params["on"]
                return {"ok": True, "state": self.state, "blocks": [{"type": "text", "text": f"lamp on={self.on}"}]}
        if capability == "brightness":
            _only(params, {"level"})
            brightness = _integer("level", params.get("level"), 0, 100)
            with self.lock:
                self.brightness = brightness
            return {
                "ok": True,
                "state": self.state,
                "blocks": [{"type": "value", "name": "level", "value": brightness, "unit": "%"}],
            }
        raise MhsError(404, "unknown_capability", f"{self.device_id} cannot write {capability!r}")

    def health(self) -> dict[str, Any]:
        return {"state": self.state, "healthy": True, "detail": "simulated lamp responding", "checks": []}


def _integer(name: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise MhsError(400, "bad_parameter", f"{name} must be an integer within [{low}, {high}]")
    return value


def _only(params: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(params) - allowed
    if unknown:
        raise MhsError(400, "bad_parameter", f"Unknown parameters: {', '.join(sorted(unknown))}")


def build_server(host: str = "127.0.0.1", port: int = 0, token: str | None = None, verbose: bool = False):
    """Create isolated device instances; port=0 picks a free port for tests."""
    return AdapterServer((host, port), [MockCamera(), MockLamp()], token=token, verbose=verbose)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mock MHS-HTTP/1 adapter (two simulated devices)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8800, help="bind port (default 8800; 0 picks a free one)")
    parser.add_argument("--token", default=None, help="require this bearer token on every request")
    parser.add_argument("--verbose", action="store_true", help="log each request to stderr")
    args = parser.parse_args(argv)

    server = build_server(args.host, args.port, args.token, args.verbose)
    host, port = server.server_address[:2]
    print(f"mock MHS adapter on http://{host}:{port}{BASE_PATH}  devices: {', '.join(server.devices)}", flush=True)
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
