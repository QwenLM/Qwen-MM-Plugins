"""MHS-HTTP/1 server: install msgpack and copy beside a device implementation.

Devices implement summary(), meta(), health(), read(capability, params), and
write(capability, params). An optional reset(params) handles soft/estop requests.
This module owns only HTTP, MessagePack, authentication, and device routing. Parameter
validation, execution, cancellation, and resource cleanup belong to the device.
"""

from __future__ import annotations

import hmac
import json
import math
import socket
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

import msgpack

BASE_PATH = "/mhs/v1"
MAX_REQUEST_BYTES = 65536
CONTENT_TYPE = "application/msgpack"


class MhsError(Exception):
    """An HTTP status and structured MHS error, safe to return to the caller."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


class Device(Protocol):
    device_id: str

    def summary(self) -> dict[str, Any]: ...
    def meta(self) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...
    def read(self, capability: str, params: dict[str, Any]) -> dict[str, Any]: ...
    def write(self, capability: str, params: dict[str, Any]) -> dict[str, Any]: ...


def text_result(ok: bool, state: str, **values: Any) -> dict[str, Any]:
    block = {"type": "text", "text": json.dumps(values, allow_nan=False)}
    return {"ok": ok, "state": state, "blocks": [block]}


def _validate(value: Any, depth: int = 0) -> None:
    """Keep the wire data finite, string-keyed, and bounded in nesting; never coerce parameters."""
    if depth > 64:
        raise ValueError("MessagePack nesting exceeds 64 levels")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("MessagePack map keys must be strings")
            _validate(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate(item, depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("MessagePack numbers must be finite")
    elif value is not None and not isinstance(value, (str, bytes, bool, int)):
        raise ValueError(f"Unsupported MessagePack value: {type(value).__name__}")


class AdapterServer(ThreadingHTTPServer):
    """Each server owns its device registry; devices own their concurrency policy."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        devices: Iterable[Device],
        *,
        token: str | None = None,
        verbose: bool = False,
        request_timeout_s: float = 10,
    ):
        self.devices: dict[str, Device] = {}
        for device in devices:
            if not isinstance(device.device_id, str) or not device.device_id.strip():
                raise ValueError("A device must have a non-empty string device_id")
            if device.device_id in self.devices:
                raise ValueError(f"Duplicate device_id: {device.device_id}")
            self.devices[device.device_id] = device
        self.token = token
        self.verbose = verbose
        self.request_timeout_s = request_timeout_s
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.request_timeout_s)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    server: AdapterServer
    server_version = "mhs-adapter/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.verbose:
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            if self.server.token:
                actual = self.headers.get("Authorization", "").encode("utf-8")
                expected = f"Bearer {self.server.token}".encode("utf-8")
                if not hmac.compare_digest(actual, expected):
                    raise MhsError(401, "unauthorized", "Missing or wrong bearer token")
            result = self._route(method)
            self._send(200, result)
        except MhsError as exc:
            self._send(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
        except socket.timeout:
            self._send(408, {"error": {"code": "request_timeout", "message": "Request body timed out"}})
        except Exception as exc:  # noqa: BLE001 - answer device failures with the protocol error shape
            self._send(500, {"error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}})

    def _body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != CONTENT_TYPE:
            raise MhsError(415, "unsupported_media_type", f"Use Content-Type: {CONTENT_TYPE}")
        if self.headers.get("Transfer-Encoding"):
            raise MhsError(400, "bad_messagepack", "Use Content-Length; chunked requests are not supported")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            raise MhsError(400, "bad_messagepack", "Multiple Content-Length headers")
        try:
            length = int(lengths[0]) if lengths else 0
        except ValueError:
            raise MhsError(400, "bad_messagepack", "Invalid Content-Length") from None
        if length < 0:
            raise MhsError(400, "bad_messagepack", "Content-Length must not be negative")
        if length > MAX_REQUEST_BYTES:
            raise MhsError(413, "body_too_large", f"Request body exceeds {MAX_REQUEST_BYTES} bytes")
        if not length:
            return {}
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise MhsError(400, "bad_messagepack", "Incomplete request body")
        try:
            body = msgpack.unpackb(
                raw,
                raw=False,
                strict_map_key=True,
                max_str_len=MAX_REQUEST_BYTES,
                max_bin_len=MAX_REQUEST_BYTES,
                max_array_len=MAX_REQUEST_BYTES,
                max_map_len=MAX_REQUEST_BYTES,
                max_ext_len=0,
            )
            _validate(body)
        except (ValueError, msgpack.UnpackException) as exc:
            raise MhsError(400, "bad_messagepack", f"Request body is not valid MessagePack: {exc}") from None
        if not isinstance(body, dict):
            raise MhsError(400, "bad_messagepack", "Request body must be a MessagePack map")
        return body

    def _route(self, method: str) -> dict[str, Any]:
        path = urlsplit(self.path).path
        if not path.startswith(BASE_PATH + "/"):
            raise MhsError(404, "not_found", f"Use {BASE_PATH}/devices")
        segments = [unquote(part) for part in path[len(BASE_PATH) + 1 :].rstrip("/").split("/")]
        if segments == ["devices"] and method == "GET":
            return {"devices": [device.summary() for device in self.server.devices.values()]}
        if len(segments) < 2 or segments[0] != "devices":
            raise MhsError(404, "not_found", "Unknown endpoint")
        device = self.server.devices.get(segments[1])
        if device is None:
            raise MhsError(404, "unknown_device", f"Unknown device: {segments[1]}")
        rest = segments[2:]
        if method == "GET":
            if not rest:
                return device.meta()
            if rest == ["health"]:
                return device.health()
        if method == "POST":
            if rest == ["reset"]:
                params = self._body()
                if set(params) - {"mode"} or params.get("mode", "soft") not in ("soft", "estop"):
                    raise MhsError(400, "bad_parameter", "reset accepts only mode=soft or mode=estop")
                reset = getattr(device, "reset", None)
                if reset is None:
                    raise MhsError(405, "reset_unsupported", f"{device.device_id} does not implement reset")
                return reset(params)
            if len(rest) == 2 and rest[0] in ("read", "write"):
                return getattr(device, rest[0])(rest[1], self._body())
        raise MhsError(404, "not_found", "Unknown endpoint")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise TypeError("Device response must be a MessagePack map")
        _validate(payload)
        body = msgpack.packb(payload, use_bin_type=True)
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
