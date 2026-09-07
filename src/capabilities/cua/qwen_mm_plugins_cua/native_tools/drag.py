"""Drag between two primary-display screenshot points."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from qwen_mm_plugins_cua.mode import COORDINATE_MAX_INCLUSIVE
from qwen_mm_plugins_cua.native_tools._desktop import execute_desktop_action
from qwen_mm_plugins_cua.native_tools._schemas import SnapshotArgs


class DragArgs(SnapshotArgs):
    from_x: float = Field(ge=0, le=COORDINATE_MAX_INCLUSIVE, description="Start X in the current coordinate_space.")
    from_y: float = Field(ge=0, le=COORDINATE_MAX_INCLUSIVE, description="Start Y in the current coordinate_space.")
    to_x: float = Field(ge=0, le=COORDINATE_MAX_INCLUSIVE, description="End X in the current coordinate_space.")
    to_y: float = Field(ge=0, le=COORDINATE_MAX_INCLUSIVE, description="End Y in the current coordinate_space.")
    duration_ms: int = Field(default=500, ge=0, le=10000, description="Drag-path duration.")
    steps: int = Field(default=20, ge=1, le=200, description="Number of interpolated drag events.")
    button: Literal["left", "right", "middle"] = Field(default="left", description="Pointer button.")
    modifiers: list[str] | None = Field(default=None, description="Modifiers held during the drag.")


TOOL: dict[str, Any] = {
    "name": "drag",
    "description": "Drag between two points in the current primary-display coordinate_space.",
    "args": DragArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute_desktop_action("drag", arguments)
