"""Wait for a primary-display visual condition."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.native_tools._desktop import wait_for_visual
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class WaitArgs(StrictArgs):
    condition: Literal["state_changed", "stable"] = Field(description="Visual condition to wait for.")
    snapshot_id: str | None = Field(
        default=None,
        pattern=r"^s[0-9a-f]{8}$",
        description="Baseline desktop snapshot; required for state_changed and recommended for stable.",
    )
    timeout_seconds: float = Field(default=10, ge=0.1, le=30, description="Maximum bounded wait.")
    poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=5, description="Polling interval.")
    stable_for_seconds: float = Field(default=1, ge=0.2, le=10, description="Unchanged duration for stable.")

    @model_validator(mode="after")
    def validate_condition_inputs(self):
        if self.condition == "state_changed" and not self.snapshot_id:
            raise ValueError("condition=state_changed requires snapshot_id")
        return self


TOOL: dict[str, Any] = {
    "name": "wait",
    "description": "Wait for a bounded primary-display visual condition and return the final screenshot.",
    "args": WaitArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return wait_for_visual(arguments)
