"""Type text into the frontmost application's focused control."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from qwen_mm_plugins_cua.native_tools._desktop import execute_desktop_action
from qwen_mm_plugins_cua.native_tools._schemas import SnapshotArgs


class TypeTextArgs(SnapshotArgs):
    text: str = Field(description="Literal text to send to the focused control.")
    delay_ms: int = Field(default=30, ge=0, le=200, description="Delay between synthesized characters.")


TOOL: dict[str, Any] = {
    "name": "type_text",
    "description": "Type into the frontmost focused control, then return a fresh primary-display screenshot.",
    "args": TypeTextArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute_desktop_action("type_text", arguments)
