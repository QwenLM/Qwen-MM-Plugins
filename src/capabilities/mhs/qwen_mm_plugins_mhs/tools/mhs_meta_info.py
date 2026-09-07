"""Everything static about one device: what it is, what it can do, and what it will refuse."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.content import json_text, text

from ..errors import guarded
from ..registry import meta_of


class MetaInfoArgs(BaseModel):
    device_id: str = Field(
        description=(
            "Device to describe, as reported by mhs_discover ('<adapter>/<device_id>'). A bare "
            "device id also works when only one adapter has it."
        )
    )
    refresh: bool = Field(
        default=False,
        description="Re-query the adapter instead of using cached metadata (e.g. after a firmware change).",
    )


TOOL: dict[str, Any] = {
    "name": "mhs_meta_info",
    "description": (
        "Full metadata for one device: identity (manufacturer, model, serial, firmware), a natural-"
        "language description, every capability with its direction/unit/parameters, and the safety "
        "limits the device declares. Read this before the first mhs_write to a device — it is how you "
        "learn the permitted ranges and which capabilities need confirmation."
    ),
    "args": MetaInfoArgs,
}


def _number(value: float) -> str:
    """Limits are normalized to float; show 100 rather than 100.0 so a range reads like a range."""
    return str(int(value)) if value == int(value) else str(value)


@guarded
def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    _, _, meta = meta_of(arguments["device_id"], refresh=bool(arguments.get("refresh")))
    blocks: list[dict[str, Any]] = [json_text(meta)]

    # Call out the limits explicitly. Buried in JSON they are easy to skim past, and they are the one
    # part of this record where being wrong has physical consequences.
    hard = [limit for limit in meta["safety_limits"] if limit["hard"]]
    if hard:
        lines = [f"{meta['device_id']} enforces {len(hard)} hard safety limit(s); writes outside them are refused:"]
        for limit in hard:
            unit = f" {limit['unit']}" if limit["unit"] else ""
            low = "-inf" if limit["min"] is None else _number(limit["min"])
            high = "+inf" if limit["max"] is None else _number(limit["max"])
            lines.append(f"  {limit['parameter']}: [{low}, {high}]{unit}")
        blocks.append(text("\n".join(lines)))
    if not meta["capabilities"]:
        blocks.append(text(f"{meta['device_id']} reports no capabilities, so it can be neither read nor commanded."))
    return blocks
