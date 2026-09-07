"""Command a device — the one tool here that changes the physical world.

Safety limits and confirmation are checked in this process, before the request is sent. The device is
expected to enforce its own limits too; this is the layer that means a bad guess costs a round trip
instead of a machine.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.content import text

from ..errors import guarded
from ..http_client import request_json, segment
from ..protocol import check_write, to_content_blocks
from ..registry import meta_of


class WriteArgs(BaseModel):
    device_id: str = Field(
        description=(
            "Device to command, as reported by mhs_discover ('<adapter>/<device_id>'). A bare device "
            "id also works when only one adapter has it."
        )
    )
    capability: str = Field(
        description="Capability name to write, exactly as listed by mhs_meta_info (e.g. 'settings', 'power')."
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The command's parameters, as documented in the capability's metadata "
            "(e.g. {'exposure': 40}). Values are checked against the device's declared safety limits."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Set true only to proceed with a write the device flagged as needing confirmation, or one "
            "that exceeds a SOFT limit. Hard limits cannot be overridden by this flag. Do not set it "
            "speculatively — it exists so a human-consequential action is deliberate."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "mhs_write",
    "description": (
        "Send a command to a physical device (move, set, switch on/off). THIS AFFECTS REAL HARDWARE. "
        "Call mhs_meta_info first to learn the capability's parameters and permitted ranges. Writes "
        "outside a declared hard safety limit are refused; capabilities marked as requiring "
        "confirmation need confirm=true. If a write fails, call mhs_health_check before retrying."
    ),
    "args": WriteArgs,
}


@guarded
def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    capability = arguments["capability"]
    params = arguments.get("params") or {}
    confirm = bool(arguments.get("confirm"))

    adapter, local_id, meta = meta_of(arguments["device_id"])
    if problem := check_write(meta, capability, params, confirm):
        return [text(f"Error: {problem}")]

    where = f"{meta['device_id']} write {capability!r}"
    payload = request_json(
        adapter,
        "POST",
        f"/devices/{segment(local_id)}/write/{segment(capability)}",
        params,
    )

    # An adapter can answer HTTP 200 and still report the command as rejected; say so plainly rather
    # than letting a quiet "ok": false read as success.
    accepted = payload.get("ok") is not False
    state = payload.get("state")
    headline = f"{where}: {'accepted' if accepted else 'REJECTED by the device'}"
    if isinstance(state, str) and state:
        headline += f" (device state: {state})"

    blocks: list[dict[str, Any]] = [text(headline)]
    blocks.extend(to_content_blocks(payload.get("blocks"), where))
    if not accepted:
        blocks.append(text("The device did not carry out this command. Check mhs_health_check before retrying."))
    return blocks
