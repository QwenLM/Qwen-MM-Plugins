"""Type into one current editable browser ref."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from qwen_mm_plugins_cua.browser_tools._shared import BrowserTargetArgs, call_driver_tool


class BrowserTypeArgs(BrowserTargetArgs):
    ref: str = Field(description="Current editable page ref from get_browser_state.")
    text: str = Field(description="Literal text to insert.")
    replace: bool = Field(default=False, description="Replace the field's content instead of appending.")
    mode: Literal["insert_text", "keystrokes"] = "insert_text"


TOOL: dict[str, Any] = {
    "name": "browser_type",
    "description": "Type into one current editable page ref through an exactly bound tab.",
    "args": BrowserTypeArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_type", arguments)
