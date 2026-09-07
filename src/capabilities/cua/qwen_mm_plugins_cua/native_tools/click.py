"""Click one point in the current primary-display screenshot."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.native_tools._desktop import execute_desktop_action
from qwen_mm_plugins_cua.native_tools._schemas import PointArgs


class ClickArgs(PointArgs):
    button: Literal["left", "right", "middle"] = Field(default="left", description="Pointer button.")
    count: Literal[1, 2] = Field(default=1, description="Single or double click.")
    modifiers: list[str] | None = Field(default=None, description="Optional cmd/shift/option/ctrl modifiers.")

    @model_validator(mode="after")
    def validate_click(self):
        if self.count == 2 and (self.button != "left" or self.modifiers):
            raise ValueError("double click supports only an unmodified left click")
        return self


TOOL: dict[str, Any] = {
    "name": "click",
    "description": "Click one point in the current primary-display coordinate_space and return a fresh screenshot.",
    "args": ClickArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute_desktop_action("click", arguments)
