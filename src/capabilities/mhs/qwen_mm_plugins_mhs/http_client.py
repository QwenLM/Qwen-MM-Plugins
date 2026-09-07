"""HTTP+JSON transport to an MHS adapter — the one place this host touches the network.

Built on stdlib urllib so the capability needs no Python dependencies at all: an MHS host often runs
on the same constrained box as the hardware. Every failure mode an adapter can present (unreachable,
timeout, HTTP error, non-JSON body, absurdly large body) comes back as one AdapterError carrying a
message fit to hand to the model.

Keeping all of it behind request_json is also the seam a second transport (gRPC, stdio) would slot
into later without touching the tools.

Named http_client rather than transport on purpose: mcp_framework reads an optional `transport`
attribute off the server package as a transport factory, so a module of that name shadows it and
crashes the server at startup.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from shared.env import MAX_RESPONSE_BYTES

from .config import Adapter

# A tool result cannot exceed MAX_RESPONSE_BYTES anyway, so refusing a larger adapter body early
# keeps a misbehaving (or hostile) adapter from ballooning this process's memory.
MAX_BODY_BYTES = MAX_RESPONSE_BYTES

_USER_AGENT = "qwen-mm-plugins-mhs/1"


class AdapterError(Exception):
    """An adapter call failed. `status` is the HTTP status when there was one."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


# Adapters address hardware, which is normally on the LAN or loopback. An ambient HTTP_PROXY /
# http_proxy would otherwise silently route those requests through a proxy that cannot reach the
# device — a confusing failure that looks like broken hardware. urllib installs proxy handling by
# default, so build an opener that explicitly has none.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def segment(value: str) -> str:
    """Escape a device id or capability name for use as one URL path segment.

    Device ids and capability names come from the adapter and from the model, so they can contain
    spaces or slashes; interpolating them raw would silently change which path is requested.
    """
    return urllib.parse.quote(value, safe="")


def _headers(adapter: Adapter, *, has_body: bool) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if has_body:
        headers["Content-Type"] = "application/json"
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


def _decode(body: bytes, where: str) -> dict[str, Any]:
    if not body.strip():
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = body[:200].decode("utf-8", "replace")
        raise AdapterError(f"{where} did not return JSON ({exc}); body starts: {preview!r}") from None
    if not isinstance(payload, dict):
        raise AdapterError(f"{where} returned a JSON {type(payload).__name__}, expected an object")
    return payload


def _error_from_response(status: int, body: bytes, where: str) -> AdapterError:
    """Turn an HTTP error body into an AdapterError, preferring the protocol's error object."""
    code = None
    detail = ""
    try:
        payload = json.loads(body.decode("utf-8"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            code = error.get("code") if isinstance(error.get("code"), str) else None
            message = error.get("message")
            detail = message if isinstance(message, str) else ""
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        detail = body[:200].decode("utf-8", "replace")
    suffix = f": {detail}" if detail else ""
    label = f" [{code}]" if code else ""
    return AdapterError(f"{where} returned HTTP {status}{label}{suffix}", status=status, code=code)


def request_json(
    adapter: Adapter,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call one MHS protocol path on `adapter` and return the decoded JSON object.

    `payload` is sent as a JSON body (POST); pass None for a bodyless GET. Raises AdapterError,
    never a urllib exception, so callers have exactly one thing to catch.
    """
    url = adapter.endpoint(path)
    where = f"adapter {adapter.name!r} ({method} {url})"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method, headers=_headers(adapter, has_body=data is not None)
    )

    try:
        with _opener.open(request, timeout=adapter.timeout) as response:
            return _decode(_read_capped(response, where), where)
    except urllib.error.HTTPError as exc:
        # HTTPError is itself a response object, so the error body is readable here.
        try:
            body = _read_capped(exc, where)
        except (AdapterError, OSError):
            body = b""
        raise _error_from_response(exc.code, body, where) from None
    except socket.timeout:
        raise AdapterError(f"{where} timed out after {adapter.timeout:g}s") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise AdapterError(f"{where} timed out after {adapter.timeout:g}s") from None
        raise AdapterError(f"{where} is unreachable: {reason}") from None
    except OSError as exc:
        raise AdapterError(f"{where} failed: {exc}") from None
