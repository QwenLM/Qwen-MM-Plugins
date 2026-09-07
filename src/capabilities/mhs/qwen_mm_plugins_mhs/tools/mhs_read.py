"""Read one capability off a device — a sensor value, a setting, or a camera frame."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.content import text

from ..errors import guarded
from ..http_client import request_json, segment
from ..protocol import check_read, to_content_blocks
from ..registry import meta_of


class ReadArgs(BaseModel):
    device_id: str = Field(
        description=(
            "Device to read, as reported by mhs_discover ('<adapter>/<device_id>'). A bare device id "
            "also works when only one adapter has it."
        )
    )
    capability: str = Field(
        description="Capability name to read, exactly as listed by mhs_discover or mhs_meta_info (e.g. 'frame')."
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional per-read parameters the capability documents in its metadata "
            "(e.g. {'format': 'png'} for a camera frame). Omit when the capability takes none."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "mhs_read",
    "description": (
        "Read a capability from a physical device: a sensor value, a current setting, or an image "
        "(a camera frame comes back as a viewable image). Read-only — it never changes device state. "
        "Use mhs_discover for capability names; this refuses a name the device does not declare."
    ),
    "args": ReadArgs,
}


@guarded
def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    capability = arguments["capability"]
    params = arguments.get("params") or {}

    adapter, local_id, meta = meta_of(arguments["device_id"])
    # Validate against the device's own declaration first: a typo costs no round trip, and the error
    # can name the capabilities that do exist.
    if problem := check_read(meta, capability):
        return [text(f"Error: {problem}")]

    where = f"{meta['device_id']} read {capability!r}"
    payload = request_json(
        adapter,
        "POST",
        f"/devices/{segment(local_id)}/read/{segment(capability)}",
        params,
    )
    blocks = to_content_blocks(payload.get("blocks"), where)
    if not blocks:
        return [text(f"{where} succeeded but returned no content.")]
    return blocks
