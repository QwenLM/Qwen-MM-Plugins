"""Capture the primary desktop without an accessibility walk."""

from __future__ import annotations

from typing import Any

from qwen_mm_plugins_cua.browser_tools._shared import call_driver_tool
from qwen_mm_plugins_cua.driver import DEFAULT_SESSION
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class GetDesktopStateArgs(StrictArgs):
    session: str = DEFAULT_SESSION


TOOL: dict[str, Any] = {
    "name": "get_desktop_state",
    "description": "Capture the primary display in its native PNG pixel frame without walking AX state.",
    "args": GetDesktopStateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("get_desktop_state", arguments)
