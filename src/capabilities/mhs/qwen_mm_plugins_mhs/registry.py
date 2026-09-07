"""Device registry: which adapter owns which device, plus a short-lived cache of what they report.

Device ids are reported to the model *qualified* as "<adapter>/<device_id>", which stays stable when
a second adapter joins and happens to use the same local id. A bare id is still accepted when it is
unambiguous, because that is what a model will naturally type back.

Metadata and the device list are cached briefly: every read/write validates against the device's
declared capabilities and safety limits, so without a cache each hardware call would cost two HTTP
round trips. Live state is deliberately NOT cached — health_check always hits the device.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from shared.env import get_env

from .config import Adapter, load_adapters
from .http_client import AdapterError, request_json, segment
from .protocol import normalize_device_summary, normalize_meta


def cache_ttl() -> float:
    """Seconds to trust a cached device list / metadata. 0 disables caching."""
    raw = get_env("QWEN_MM_MHS_CACHE_TTL", "60")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 60.0


class _Cache:
    """Tiny TTL cache. Handlers run on the framework's worker threads, hence the lock."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        ttl = cache_ttl()
        if ttl <= 0:
            return None
        with self._lock:
            hit = self._entries.get(key)
        if hit is None or time.monotonic() - hit[0] > ttl:
            return None
        return hit[1]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_cache = _Cache()


def invalidate() -> None:
    """Drop every cached device list and metadata record (the tools' `refresh=true`)."""
    _cache.clear()


def adapters() -> list[Adapter]:
    """Configured adapters. Raises config.ConfigError with an actionable message."""
    return load_adapters()


def devices_of(adapter: Adapter, *, refresh: bool = False) -> list[dict[str, Any]]:
    """Device summaries reported by one adapter. Raises AdapterError if it cannot be reached."""
    key = f"devices::{adapter.name}"
    if not refresh:
        cached = _cache.get(key)
        if cached is not None:
            return cached
    payload = request_json(adapter, "GET", "/devices")
    raw = payload.get("devices")
    if not isinstance(raw, list):
        raise AdapterError(f"adapter {adapter.name!r} did not return a 'devices' list")
    summaries = [s for s in (normalize_device_summary(entry, adapter.name) for entry in raw) if s is not None]
    _cache.put(key, summaries)
    return summaries


def all_devices(*, refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Every device across every adapter, plus per-adapter errors.

    Errors are returned rather than raised: one unplugged camera must not hide the arm that is still
    working, and the model needs to see both facts at once.
    """
    found: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for adapter in adapters():
        try:
            found.extend(devices_of(adapter, refresh=refresh))
        except AdapterError as exc:
            failures[adapter.name] = str(exc)
    return found, failures


def resolve(device_id: str, *, refresh: bool = False) -> tuple[Adapter, str]:
    """Map a device id the model supplied onto (adapter, adapter-local id).

    Accepts the qualified "<adapter>/<device_id>" form, or a bare device_id when exactly one adapter
    reports it. An ambiguous bare id is an error naming the qualified alternatives, so the model's
    next call can be right rather than a guess.
    """
    device_id = (device_id or "").strip()
    if not device_id:
        raise LookupError("device_id is required. Call mhs_discover to list the devices that exist.")

    configured = {a.name: a for a in adapters()}

    if "/" in device_id:
        adapter_name, _, local_id = device_id.partition("/")
        adapter = configured.get(adapter_name)
        if adapter is None:
            known = ", ".join(sorted(configured)) or "none configured"
            raise LookupError(f"no adapter named {adapter_name!r}. Configured adapters: {known}.")
        if not local_id:
            raise LookupError(f"{device_id!r} names an adapter but no device on it.")
        return adapter, local_id

    matches = [d for d in all_devices(refresh=refresh)[0] if d["adapter_device_id"] == device_id]
    if not matches:
        raise LookupError(
            f"no device {device_id!r} on any configured adapter. Call mhs_discover to list what is reachable."
        )
    if len(matches) > 1:
        qualified = ", ".join(sorted(m["device_id"] for m in matches))
        raise LookupError(f"{device_id!r} exists on more than one adapter. Use one of: {qualified}.")
    return configured[matches[0]["adapter"]], device_id


def meta_of(device_id: str, *, refresh: bool = False) -> tuple[Adapter, str, dict[str, Any]]:
    """Resolve `device_id` and fetch its (cached) DeviceMeta."""
    adapter, local_id = resolve(device_id, refresh=refresh)
    qualified = f"{adapter.name}/{local_id}"
    key = f"meta::{qualified}"
    if not refresh:
        cached = _cache.get(key)
        if cached is not None:
            return adapter, local_id, cached
    meta = normalize_meta(request_json(adapter, "GET", f"/devices/{segment(local_id)}"), qualified)
    _cache.put(key, meta)
    return adapter, local_id, meta
