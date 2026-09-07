"""Move the global pointer within the current primary-display screenshot."""

from __future__ import annotations

from typing import Any

from qwen_mm_plugins_cua.native_tools._desktop import execute_desktop_action
from qwen_mm_plugins_cua.native_tools._schemas import PointArgs


class MoveCursorArgs(PointArgs):
    pass


TOOL: dict[str, Any] = {
    "name": "move_cursor",
    "description": "Move the pointer to a point in the current coordinate_space, then return a fresh screenshot.",
    "args": MoveCursorArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute_desktop_action("move_cursor", arguments)
