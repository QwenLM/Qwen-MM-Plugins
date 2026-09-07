"""Is the hardware actually there and working right now.

Always hits the device — health is the one thing never served from cache.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.content import json_text, text

from ..errors import guarded
from ..http_client import AdapterError, request_json, segment
from ..registry import all_devices, resolve


class HealthCheckArgs(BaseModel):
    device_id: str | None = Field(
        default=None,
        description=(
            "Device to check ('<adapter>/<device_id>', or a bare device id when unambiguous). "
            "Omit to check every reachable device — the right call when you do not yet know what is wrong."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "mhs_health_check",
    "description": (
        "Check live device health: state (online/busy/offline/error/maintenance), whether it is "
        "healthy, and any per-subsystem checks the device reports. Never cached. Use this after a "
        "failed read or write, before starting a sequence of commands, or with no device_id to survey "
        "everything at once."
    ),
    "args": HealthCheckArgs,
}


def _check_one(device_id: str) -> dict[str, Any]:
    adapter, local_id = resolve(device_id)
    payload = request_json(adapter, "GET", f"/devices/{segment(local_id)}/health")
    return {
        "device_id": f"{adapter.name}/{local_id}",
        "state": payload.get("state", "unknown"),
        "healthy": payload.get("healthy"),
        "detail": payload.get("detail", ""),
        "checks": payload.get("checks", []),
    }


@guarded
def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    requested = (arguments.get("device_id") or "").strip()
    if requested:
        return [json_text(_check_one(requested))]

    devices, unreachable = all_devices()
    results: list[dict[str, Any]] = []
    for device in devices:
        try:
            results.append(_check_one(device["device_id"]))
        except (AdapterError, LookupError) as exc:
            # One unhealthy device is exactly what this survey is for — record it and keep going.
            results.append({"device_id": device["device_id"], "healthy": False, "error": str(exc)})

    report: dict[str, Any] = {"devices": results}
    if unreachable:
        report["unreachable_adapters"] = unreachable
    blocks: list[dict[str, Any]] = [json_text(report)]
    if not results and not unreachable:
        blocks.append(text("No devices are configured, so there was nothing to check."))
    return blocks
