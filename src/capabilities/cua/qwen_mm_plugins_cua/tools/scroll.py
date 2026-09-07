"""MCP tool: scroll a focused region, semantic element, or screenshot point."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.tools._actions import DeliveredActionArgs, execute


class ScrollArgs(DeliveredActionArgs):
    direction: Literal["up", "down", "left", "right"] = Field(description="Scroll direction.")
    amount: int = Field(default=3, ge=1, le=50, description="Wheel notches or key repetitions.")
    by: Literal["line", "page"] = Field(default="line", description="Scroll granularity.")
    element_token: str | None = Field(default=None, description="Optional exact scroll target.")
    x: float | None = Field(default=None, description="Optional absolute target X in the current screenshot PNG.")
    y: float | None = Field(default=None, description="Optional absolute target Y in the current screenshot PNG.")

    @model_validator(mode="after")
    def validate_target(self):
        if self.element_token and (self.x is not None or self.y is not None):
            raise ValueError("use element_token or x/y, not both")
        if (self.x is None) != (self.y is None):
            raise ValueError("x and y must be supplied together")
        return self


TOOL: dict[str, Any] = {
    "name": "scroll",
    "description": (
        "Scroll the focused region or an exact element/point from the current snapshot, then return fresh state. "
        "Target a point for nested custom scroll surfaces."
    ),
    "args": ScrollArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute("scroll", arguments)
