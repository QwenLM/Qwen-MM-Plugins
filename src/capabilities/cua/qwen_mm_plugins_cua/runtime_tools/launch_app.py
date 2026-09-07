"""Launch a native application without foregrounding it."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.browser_tools._shared import call_driver_tool
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class LaunchAppArgs(StrictArgs):
    bundle_id: str | None = Field(default=None, description="Preferred unambiguous app bundle identifier.")
    name: str | None = Field(default=None, description="App display name used when bundle_id is absent.")
    urls: list[str] | None = Field(default=None, description="Optional file paths or URLs to open.")
    creates_new_application_instance: bool = Field(default=False, description="Force an isolated new app instance.")

    @model_validator(mode="after")
    def validate_app(self):
        if not self.bundle_id and not self.name:
            raise ValueError("bundle_id or name is required")
        return self


TOOL: dict[str, Any] = {
    "name": "launch_app",
    "description": "Launch one native application in the background and return its pid and known windows.",
    "args": LaunchAppArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("launch_app", arguments, timeout=60, add_session=False)
