"""Put a device back to a known-good state, or stop it now.

Deliberately its own tool rather than a `write` capability: "stop the machine" must be one
unambiguous call that needs no metadata lookup, no parameters, and no confirmation flag. Anything
standing between the model and a stop is a hazard.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from shared.content import text

from ..errors import guarded
from ..http_client import AdapterError, request, segment
from ..registry import invalidate, resolve


class ResetArgs(BaseModel):
    device_id: str = Field(
        description=("Device to reset ('<adapter>/<device_id>', or a bare device id when unambiguous).")
    )
    mode: Literal["soft", "estop"] = Field(
        default="soft",
        description=(
            "'soft' returns the device to its idle/known-good state, clearing an error. "
            "'estop' is an emergency stop: halt motion and output now, recovery second. "
            "Use 'estop' whenever something looks wrong and you are not sure why."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "mhs_reset",
    "description": (
        "Recover or stop a physical device. mode='soft' clears an error state and returns the device "
        "to idle; mode='estop' is an emergency stop that halts motion and output immediately. Needs no "
        "confirmation — stopping hardware is always allowed. Use after an error state, or the moment "
        "something looks wrong."
    ),
    "args": ResetArgs,
}

_UNSUPPORTED = (404, 405, 501)


@guarded
def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    mode = arguments.get("mode", "soft")
    adapter, local_id = resolve(arguments["device_id"])
    qualified = f"{adapter.name}/{local_id}"

    try:
        payload = request(adapter, "POST", f"/devices/{segment(local_id)}/reset", {"mode": mode})
    except AdapterError as exc:
        if exc.status in _UNSUPPORTED:
            return [
                text(
                    f"Error: {qualified} does not implement reset (HTTP {exc.status}). Reset is optional "
                    "in MHS. If this device needs to be stopped, do it physically or through its own "
                    "controls — and tell the user this device cannot be stopped through the model."
                )
            ]
        raise

    # State changed, so anything cached about this device may now be stale.
    invalidate()

    ok = payload.get("ok") is not False
    state = payload.get("state")
    suffix = f" Device state: {state}." if isinstance(state, str) and state else ""
    if not ok:
        return [
            text(f"Error: {qualified} refused the {mode} reset.{suffix} The device may need physical intervention.")
        ]
    label = "emergency stop" if mode == "estop" else "soft reset"
    return [text(f"{qualified}: {label} completed.{suffix}")]
