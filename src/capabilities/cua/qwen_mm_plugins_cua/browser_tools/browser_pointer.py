"""Typed hover, context click, double click, scroll, or drag."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.browser_tools._shared import BrowserTargetArgs, call_driver_tool


class BrowserPointerArgs(BrowserTargetArgs):
    action: Literal["hover", "right_click", "double_click", "scroll", "drag"]
    ref: str | None = Field(default=None, description="Current origin page ref; alternative to x/y.")
    x: float | None = None
    y: float | None = None
    destination_ref: str | None = Field(default=None, description="Current drag destination ref in the same frame.")
    to_x: float | None = None
    to_y: float | None = None
    delta_x: float | None = Field(default=None, description="Horizontal scroll delta in CSS pixels.")
    delta_y: float | None = Field(default=None, description="Vertical scroll delta in CSS pixels.")
    input_route: Literal["trusted", "dom_event"] = "trusted"

    @model_validator(mode="after")
    def validate_pointer(self):
        origin_point = self.x is not None or self.y is not None
        if origin_point and (self.x is None or self.y is None):
            raise ValueError("x and y must be supplied together")
        if self.ref and origin_point:
            raise ValueError("use ref or x+y, not both")
        if self.action != "scroll" and not self.ref and not origin_point:
            raise ValueError("an origin ref or x+y is required")
        if self.input_route == "dom_event" and not self.ref:
            raise ValueError("input_route=dom_event requires ref")
        destination_point = self.to_x is not None or self.to_y is not None
        if destination_point and (self.to_x is None or self.to_y is None):
            raise ValueError("to_x and to_y must be supplied together")
        if self.action == "drag" and bool(self.destination_ref) == destination_point:
            raise ValueError("drag requires exactly one destination: destination_ref or to_x+to_y")
        if self.action != "drag" and (self.destination_ref or destination_point):
            raise ValueError("drag destination fields are valid only for action=drag")
        if self.action == "scroll" and self.delta_x is None and self.delta_y is None:
            raise ValueError("scroll requires delta_x or delta_y")
        if self.action != "scroll" and (self.delta_x is not None or self.delta_y is not None):
            raise ValueError("scroll delta fields are valid only for action=scroll")
        return self


TOOL: dict[str, Any] = {
    "name": "browser_pointer",
    "description": "Hover, right-click, double-click, scroll, or drag in an exactly bound tab.",
    "args": BrowserPointerArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_pointer", arguments)
