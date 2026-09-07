"""Prepare an exactly owned browser DevTools endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.browser_tools._shared import call_driver_tool
from qwen_mm_plugins_cua.driver import DEFAULT_SESSION
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class BrowserProfile(StrictArgs):
    mode: Literal["isolated_new", "isolated_named"]
    name: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_name(self):
        if self.mode == "isolated_named" and not self.name:
            raise ValueError("profile.name is required for isolated_named")
        if self.mode == "isolated_new" and self.name:
            raise ValueError("profile.name is valid only for isolated_named")
        return self


class ExistingProfileStrategy(StrictArgs):
    kind: Literal["existing_profile"]


class BrowserPrepareArgs(StrictArgs):
    pid: int = Field(gt=0, description="Browser process id to prepare.")
    window_id: int | None = Field(default=None, gt=0, description="Approval anchor for an existing profile.")
    allow_launch: bool = Field(default=False, description="Allow a separate driver-owned browser launch.")
    profile: BrowserProfile | None = None
    strategy: ExistingProfileStrategy | None = None
    session: str = DEFAULT_SESSION

    @model_validator(mode="after")
    def validate_strategy(self):
        if self.strategy and self.window_id is None:
            raise ValueError("window_id is required for strategy=existing_profile")
        if self.profile and not self.allow_launch:
            raise ValueError("allow_launch=true is required when profile is supplied")
        if self.profile and self.strategy:
            raise ValueError("use profile or strategy, not both")
        return self


TOOL: dict[str, Any] = {
    "name": "browser_prepare",
    "description": "Prepare or detect an authorized exact browser DevTools endpoint without modifying a user profile.",
    "args": BrowserPrepareArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_prepare", arguments, timeout=60)
