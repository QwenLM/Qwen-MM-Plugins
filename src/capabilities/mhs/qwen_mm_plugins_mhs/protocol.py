"""MHS-HTTP/1 wire shapes: normalizing what an adapter says, and gating what we let through.

Two jobs live here.

*Reading* an adapter: everything crossing the wire is untrusted input written by someone else, so
metadata is normalized tolerantly (a missing optional field is not an error) while result blocks are
validated strictly enough that a malformed one degrades to text instead of corrupting the tool result.

*Writing* to hardware: the adapter declares its capabilities and safety limits, and this host enforces
them BEFORE the request leaves the process. That ordering is the point — an out-of-range command is
refused here rather than trusted to be refused by the device, and a bad guess by the model costs a
round trip instead of a machine.
"""

from __future__ import annotations

import os
from typing import Any

from shared.content import image, text

from .http_client import MAX_BODY_BYTES

# A capability is readable, writable, or both. Anything else an adapter sends is treated as "both",
# because refusing to talk to a device over a metadata quibble is the worse failure.
_READABLE = ("read", "both")
_WRITABLE = ("write", "both")


class ProtocolError(Exception):
    """An adapter's response does not conform to MHS-HTTP/1."""


def normalize_device_summary(raw: object, adapter_name: str) -> dict[str, Any] | None:
    """One entry of GET /devices. Returns None for an entry with no usable device_id."""
    if not isinstance(raw, dict):
        return None
    device_id = raw.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        return None
    device_id = device_id.strip()
    capabilities = raw.get("capabilities")
    return {
        "device_id": f"{adapter_name}/{device_id}",
        "adapter": adapter_name,
        "adapter_device_id": device_id,
        "device_type": _as_str(raw.get("device_type"), "unknown"),
        "state": _as_str(raw.get("state"), "unknown"),
        "summary": _as_str(raw.get("summary"), ""),
        "tags": [t for t in raw.get("tags", []) if isinstance(t, str)] if isinstance(raw.get("tags"), list) else [],
        "capabilities": [c for c in capabilities if isinstance(c, str)] if isinstance(capabilities, list) else [],
    }


def normalize_meta(raw: object, qualified_id: str) -> dict[str, Any]:
    """GET /devices/{id} → a DeviceMeta with every field present and the right type."""
    if not isinstance(raw, dict):
        raise ProtocolError(f"metadata for {qualified_id} is not a map")
    return {
        "device_id": qualified_id,
        "device_type": _as_str(raw.get("device_type"), "unknown"),
        "manufacturer": _as_str(raw.get("manufacturer"), ""),
        "model": _as_str(raw.get("model"), ""),
        "serial_number": _as_str(raw.get("serial_number"), ""),
        "firmware": _as_str(raw.get("firmware"), ""),
        "location": _as_str(raw.get("location"), ""),
        "description": _as_str(raw.get("description"), ""),
        "documentation_url": _as_str(raw.get("documentation_url"), ""),
        "state": _as_str(raw.get("state"), "unknown"),
        "tags": [t for t in raw.get("tags", []) if isinstance(t, str)] if isinstance(raw.get("tags"), list) else [],
        "capabilities": _normalize_capabilities(raw.get("capabilities")),
        "safety_limits": _normalize_limits(raw.get("safety_limits")),
    }


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _normalize_capabilities(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        # A bare string is allowed as shorthand for a read/write capability with no description.
        if isinstance(entry, str) and entry.strip():
            out.append(
                {
                    "name": entry.strip(),
                    "direction": "both",
                    "description": "",
                    "unit": "",
                    "value_type": "",
                    "params": {},
                    "requires_confirm": False,
                }
            )
            continue
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        direction = entry.get("direction")
        out.append(
            {
                "name": name.strip(),
                "direction": direction if direction in ("read", "write", "both") else "both",
                "description": _as_str(entry.get("description"), ""),
                "unit": _as_str(entry.get("unit"), ""),
                "value_type": _as_str(entry.get("value_type"), ""),
                "params": entry.get("params") if isinstance(entry.get("params"), dict) else {},
                "requires_confirm": entry.get("requires_confirm") is True,
            }
        )
    return out


def _normalize_limits(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        parameter = entry.get("parameter")
        if not isinstance(parameter, str) or not parameter.strip():
            continue
        out.append(
            {
                "parameter": parameter.strip(),
                "min": _as_number(entry.get("min")),
                "max": _as_number(entry.get("max")),
                "unit": _as_str(entry.get("unit"), ""),
                "description": _as_str(entry.get("description"), ""),
                # Absent means hard: a limit whose strictness the adapter forgot to state is the one
                # you least want to treat as advisory.
                "hard": entry.get("hard") is not False,
            }
        )
    return out


def _as_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def format_number(value: float) -> str:
    """Limits are normalized to float; show 100 rather than 100.0 in text meant for the model."""
    return str(int(value)) if value == int(value) else str(value)


def capability_names(meta: dict[str, Any]) -> list[str]:
    return [c["name"] for c in meta["capabilities"]]


def _find(meta: dict[str, Any], capability: str) -> dict[str, Any] | None:
    for entry in meta["capabilities"]:
        if entry["name"] == capability:
            return entry
    return None


def check_read(meta: dict[str, Any], capability: str) -> str | None:
    """Message explaining why this read cannot be attempted, or None when it can."""
    entry = _find(meta, capability)
    if entry is None:
        return _unknown_capability(meta, capability)
    if entry["direction"] not in _READABLE:
        return f"capability {capability!r} on {meta['device_id']} is write-only"
    return None


def check_write(meta: dict[str, Any], capability: str, params: dict[str, Any], confirm: bool) -> str | None:
    """Message explaining why this write must not be sent, or None when it may be.

    Order matters: existence, then direction, then safety limits, then confirmation. The model gets
    the most actionable objection first rather than being told to confirm a write that could never
    have been valid.
    """
    entry = _find(meta, capability)
    if entry is None:
        return _unknown_capability(meta, capability)
    if entry["direction"] not in _WRITABLE:
        return f"capability {capability!r} on {meta['device_id']} is read-only"

    for limit in meta["safety_limits"]:
        breach = _limit_breach(limit, params)
        if breach is None:
            continue
        if limit["hard"]:
            return (
                f"refused: {breach} This is a hard safety limit declared by the device and cannot be "
                f"overridden. Read {meta['device_id']} metadata for the permitted range."
            )
        if not confirm:
            return (
                f"refused: {breach} This is a soft limit — pass confirm=true to proceed anyway, "
                "but only after checking with whoever operates this hardware."
            )

    if entry["requires_confirm"] and not confirm:
        return (
            f"capability {capability!r} on {meta['device_id']} is marked as requiring confirmation. "
            "Re-issue with confirm=true once you are sure this should happen to the physical device."
        )
    return None


def _unknown_capability(meta: dict[str, Any], capability: str) -> str:
    known = capability_names(meta)
    available = ", ".join(known) if known else "none reported"
    return f"{meta['device_id']} has no capability {capability!r}. Available: {available}."


def _limit_breach(limit: dict[str, Any], params: dict[str, Any]) -> str | None:
    """Describe how `params` violates one safety limit, or None if it doesn't."""
    name = limit["parameter"]
    if name not in params:
        return None
    value = params[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    unit = f" {limit['unit']}" if limit["unit"] else ""
    low, high = limit["min"], limit["max"]
    if low is not None and value < low:
        return f"{name}={value}{unit} is below the declared minimum {format_number(low)}{unit}."
    if high is not None and value > high:
        return f"{name}={value}{unit} is above the declared maximum {format_number(high)}{unit}."
    return None


def to_content_blocks(raw: object, where: str) -> list[dict[str, Any]]:
    """Convert an adapter's result blocks into MCP content blocks.

    An unrecognized or malformed block becomes text rather than being dropped — losing a reading
    silently is worse than showing the model something it has to interpret.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProtocolError(f"{where} returned 'blocks' as {type(raw).__name__}, expected a list")
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            out.append(text(str(entry)))
            continue
        kind = entry.get("type")
        if kind == "text":
            out.append(text(_as_str(entry.get("text"), "")))
        elif kind == "value":
            out.append(text(_render_value(entry)))
        elif kind == "image":
            out.append(_render_image(entry, where))
        else:
            out.append(text(f"[unrecognized block type {kind!r}] {entry}"))
    return out


def _render_value(entry: dict[str, Any]) -> str:
    name = _as_str(entry.get("name"), "value")
    unit = _as_str(entry.get("unit"), "")
    rendered = f"{name} = {entry.get('value')}"
    return f"{rendered} {unit}".rstrip() if unit else rendered


def _render_image(entry: dict[str, Any], where: str) -> dict[str, Any]:
    mime = _as_str(entry.get("mimeType"), "image/jpeg")
    if not mime.startswith("image/"):
        mime = "image/jpeg"

    data = entry.get("data")
    if isinstance(data, bytes) and data:
        if len(data) > MAX_BODY_BYTES:
            return text(f"[{where} sent an image of {len(data)} bytes, over the {MAX_BODY_BYTES}-byte limit]")
        return image(data, mime)
    if data is not None:
        return text(f"[{where} sent an image block whose data is not non-empty binary bytes]")

    path = entry.get("path")
    if isinstance(path, str) and path:
        # Only meaningful for an adapter sharing this filesystem (the common localhost case).
        if not os.path.isfile(path):
            return text(f"[{where} referenced image path {path!r}, which this host cannot see]")
        if os.path.getsize(path) > MAX_BODY_BYTES:
            return text(f"[{where} referenced image {path!r}, which is over the {MAX_BODY_BYTES}-byte limit]")
        try:
            with open(path, "rb") as fh:
                return image(fh.read(), mime)
        except OSError as exc:
            return text(f"[{where} referenced image path {path!r}, unreadable: {exc}]")

    return text(f"[{where} sent an image block with neither 'data' nor 'path']")
