"""HTTP + MessagePack transport to an MHS adapter.

Requests, responses, and errors are MessagePack maps; images carry raw bytes. There is no codec
negotiation, JSON fallback, redirect following, or automatic retry of hardware commands.

Named http_client rather than transport on purpose: mcp_framework reads an optional `transport`
attribute off the server package as a transport factory, so a module of that name shadows it and
crashes the server at startup.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from typing import Any

import msgpack

from shared.env import MAX_RESPONSE_BYTES

from .config import Adapter

# A tool result cannot exceed MAX_RESPONSE_BYTES anyway, so refusing a larger adapter body early
# keeps a misbehaving (or hostile) adapter from ballooning this process's memory.
MAX_BODY_BYTES = MAX_RESPONSE_BYTES
CONTENT_TYPE = "application/msgpack"

_USER_AGENT = "qwen-mm-plugins-mhs/1"


class AdapterError(Exception):
    """An adapter call failed. `status` is the HTTP status when there was one."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect must not turn one hardware operation into another HTTP request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Hardware is on loopback/LAN; ambient proxy settings must not reroute it.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def segment(value: str) -> str:
    """Escape a device id or capability name for use as one URL path segment.

    Device ids and capability names come from the adapter and from the model, so they can contain
    spaces or slashes; interpolating them raw would silently change which path is requested.
    """
    return urllib.parse.quote(value, safe="")


def _headers(adapter: Adapter, *, has_body: bool) -> dict[str, str]:
    headers = {"Accept": CONTENT_TYPE, "User-Agent": _USER_AGENT}
    if has_body:
        headers["Content-Type"] = CONTENT_TYPE
    token = adapter.token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif adapter.token_env:
        raise AdapterError(f"adapter {adapter.name!r} is configured for bearer auth but {adapter.token_env} is not set")
    return headers


def _read_capped(fh: Any, where: str) -> bytes:
    """Read at most MAX_BODY_BYTES + 1 so an oversized body is detected rather than buffered whole."""
    body = fh.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise AdapterError(f"{where} returned more than {MAX_BODY_BYTES} bytes; refusing to buffer it")
    return body


def _decode(body: bytes, where: str, content_type: str) -> dict[str, Any]:
    if content_type.partition(";")[0].strip().lower() != CONTENT_TYPE:
        raise AdapterError(f"{where} returned Content-Type {content_type!r}, expected {CONTENT_TYPE}")
    try:
        payload = msgpack.unpackb(
            body,
            raw=False,
            strict_map_key=True,
            max_str_len=MAX_BODY_BYTES,
            max_bin_len=MAX_BODY_BYTES,
            max_array_len=min(MAX_BODY_BYTES, 100_000),
            max_map_len=min(MAX_BODY_BYTES, 100_000),
            max_ext_len=0,
        )
    except (ValueError, msgpack.UnpackException) as exc:
        raise AdapterError(f"{where} did not return a valid MessagePack body ({exc})") from None
    if not isinstance(payload, dict):
        raise AdapterError(f"{where} returned a MessagePack {type(payload).__name__}, expected a map")
    return payload


def _error_from_response(status: int, body: bytes, where: str, content_type: str) -> AdapterError:
    """Turn an HTTP error body into an AdapterError, preferring the protocol's error object."""
    code = None
    detail = ""
    try:
        error = _decode(body, where, content_type).get("error")
        if isinstance(error, dict):
            code = error.get("code") if isinstance(error.get("code"), str) else None
            message = error.get("message")
            detail = message if isinstance(message, str) else ""
    except AdapterError as exc:
        detail = str(exc)
    suffix = f": {detail}" if detail else ""
    label = f" [{code}]" if code else ""
    return AdapterError(f"{where} returned HTTP {status}{label}{suffix}", status=status, code=code)


def request(
    adapter: Adapter,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call one path once. Pass a map for POST or None for a bodyless GET; raise AdapterError."""
    url = adapter.endpoint(path)
    where = f"adapter {adapter.name!r} ({method} {url})"
    try:
        data = None if payload is None else msgpack.packb(payload, use_bin_type=True)
    except (ValueError, TypeError, OverflowError) as exc:
        raise AdapterError(f"{where} cannot encode request as MessagePack: {exc}") from None
    http_request = urllib.request.Request(
        url, data=data, method=method, headers=_headers(adapter, has_body=data is not None)
    )

    try:
        with _opener.open(http_request, timeout=adapter.timeout) as response:
            return _decode(_read_capped(response, where), where, response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        # HTTPError is itself a response object, so the error body is readable here.
        with exc:
            try:
                body = _read_capped(exc, where)
            except (AdapterError, OSError, HTTPException):
                body = b""
        raise _error_from_response(exc.code, body, where, exc.headers.get("Content-Type", "")) from None
    except socket.timeout:
        raise AdapterError(f"{where} timed out after {adapter.timeout:g}s") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise AdapterError(f"{where} timed out after {adapter.timeout:g}s") from None
        raise AdapterError(f"{where} is unreachable: {reason}") from None
    except (OSError, HTTPException) as exc:
        raise AdapterError(f"{where} failed: {exc}") from None
