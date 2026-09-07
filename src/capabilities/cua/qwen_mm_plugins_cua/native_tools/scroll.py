"""Scroll at one primary-display screenshot point."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from qwen_mm_plugins_cua.native_tools._desktop import execute_desktop_action
from qwen_mm_plugins_cua.native_tools._schemas import PointArgs


class ScrollArgs(PointArgs):
    direction: Literal["up", "down", "left", "right"] = Field(description="Scroll direction.")
    amount: int = Field(default=3, ge=1, le=50, description="Wheel notches.")
    by: Literal["line", "page"] = Field(default="line", description="Scroll granularity.")


TOOL: dict[str, Any] = {
    "name": "scroll",
    "description": "Scroll at one point in the current primary-display coordinate_space.",
    "args": ScrollArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute_desktop_action("scroll", arguments)
