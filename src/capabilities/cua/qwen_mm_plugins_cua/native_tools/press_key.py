"""Send a key or hotkey to the frontmost application."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from qwen_mm_plugins_cua.native_tools._desktop import execute_desktop_action
from qwen_mm_plugins_cua.native_tools._schemas import SnapshotArgs


class PressKeyArgs(SnapshotArgs):
    key: str = Field(min_length=1, description="Key name such as return, tab, escape, down, or a letter.")
    modifiers: list[str] | None = Field(default=None, description="Optional cmd/shift/option/ctrl/fn modifiers.")
    repeat: int = Field(default=1, ge=1, le=50, description="Number of key presses.")


TOOL: dict[str, Any] = {
    "name": "press_key",
    "description": "Send a key or hotkey to the frontmost application, then return a fresh primary-display screenshot.",
    "args": PressKeyArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute_desktop_action("press_key", arguments)
