"""Click a typed page ref or exact viewport point."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.browser_tools._shared import BrowserTargetArgs, call_driver_tool


class BrowserClickArgs(BrowserTargetArgs):
    ref: str | None = Field(default=None, description="Current page ref from get_browser_state.")
    x: float | None = Field(default=None, description="Viewport X in CSS pixels.")
    y: float | None = Field(default=None, description="Viewport Y in CSS pixels.")
    input_route: Literal["trusted", "dom_event"] = "trusted"

    @model_validator(mode="after")
    def validate_target(self):
        point = self.x is not None or self.y is not None
        if bool(self.ref) == point:
            raise ValueError("use exactly one target: ref or x+y")
        if point and (self.x is None or self.y is None):
            raise ValueError("x and y must be supplied together")
        if self.input_route == "dom_event" and not self.ref:
            raise ValueError("input_route=dom_event requires ref")
        return self


TOOL: dict[str, Any] = {
    "name": "browser_click",
    "description": "Click a current typed page ref or viewport point in an exactly bound background tab.",
    "args": BrowserClickArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_click", arguments)
