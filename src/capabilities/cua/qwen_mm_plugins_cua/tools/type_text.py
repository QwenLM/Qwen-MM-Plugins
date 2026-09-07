"""MCP tool: insert text into an element, point, or focused control."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.mode import COORDINATE_MAX_INCLUSIVE
from qwen_mm_plugins_cua.tools._actions import DeliveredActionArgs, execute


class TypeTextArgs(DeliveredActionArgs):
    text: str = Field(description="Literal text to insert.")
    element_token: str | None = Field(default=None, description="Optional exact editable element handle.")
    x: float | None = Field(
        default=None,
        ge=0,
        le=COORDINATE_MAX_INCLUSIVE,
        description="Optional focus X in the current state's coordinate_space.",
    )
    y: float | None = Field(
        default=None,
        ge=0,
        le=COORDINATE_MAX_INCLUSIVE,
        description="Optional focus Y in the current state's coordinate_space.",
    )
    delay_ms: int = Field(default=30, ge=0, le=200, description="Delay for synthesized-character fallback.")

    @model_validator(mode="after")
    def validate_target(self):
        if self.element_token and (self.x is not None or self.y is not None):
            raise ValueError("use element_token or x/y, not both")
        if (self.x is None) != (self.y is None):
            raise ValueError("x and y must be supplied together")
        return self


TOOL: dict[str, Any] = {
    "name": "type_text",
    "description": (
        "Insert literal text into an exact element, screenshot point, or the focused control, then return fresh "
        "state. Use a point for custom Chromium/Electron fields that cannot be focused semantically."
    ),
    "args": TypeTextArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute("type_text", arguments)
