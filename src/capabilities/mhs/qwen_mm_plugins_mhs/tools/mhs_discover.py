"""What hardware is out there — the entry point for every other MHS tool."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.content import json_text, text

from ..errors import guarded
from ..registry import all_devices, invalidate


class DiscoverArgs(BaseModel):
    device_type: str | None = Field(
        default=None,
        description="Only list devices of this type (e.g. 'camera', 'robot_arm'). Case-insensitive.",
    )
    tag: str | None = Field(
        default=None,
        description="Only list devices carrying this tag (e.g. 'lab-a', 'production').",
    )
    refresh: bool = Field(
        default=False,
        description=(
            "Re-query every adapter instead of using the cached device list. Use after plugging in, "
            "power-cycling, or reconfiguring hardware."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "mhs_discover",
    "description": (
        "List the physical devices reachable through the configured MHS adapters, with each device's "
        "id, type, state and capability names. Call this FIRST — device ids come from here, never "
        "from a guess. Then use mhs_meta_info before writing to a device you have not touched yet."
    ),
    "args": DiscoverArgs,
}


@guarded
def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    if arguments.get("refresh"):
        invalidate()

    devices, unreachable = all_devices(refresh=bool(arguments.get("refresh")))

    wanted_type = (arguments.get("device_type") or "").strip().lower()
    if wanted_type:
        devices = [d for d in devices if d["device_type"].lower() == wanted_type]
    wanted_tag = (arguments.get("tag") or "").strip()
    if wanted_tag:
        devices = [d for d in devices if wanted_tag in d["tags"]]

    report: dict[str, Any] = {
        "devices": [
            {
                "device_id": d["device_id"],
                "device_type": d["device_type"],
                "state": d["state"],
                "summary": d["summary"],
                "tags": d["tags"],
                "capabilities": d["capabilities"],
            }
            for d in devices
        ]
    }
    if unreachable:
        # Surfaced alongside the working devices rather than raised: one dead adapter must not hide
        # the hardware that is still usable.
        report["unreachable_adapters"] = unreachable

    blocks = [json_text(report)]
    if not devices:
        detail = "No devices matched." if (wanted_type or wanted_tag) else "No adapter reported any device."
        blocks.append(
            text(
                f"{detail} An MHS adapter is run by whoever owns the hardware; this host only "
                "connects to one. Check that the adapters in the registry file are running."
            )
        )
    return blocks
