"""List top-level native windows."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from qwen_mm_plugins_cua.browser_tools._shared import call_driver_tool
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class ListWindowsArgs(StrictArgs):
    pid: int | None = Field(default=None, gt=0, description="Optional exact process filter.")
    on_screen_only: bool = Field(default=False, description="Drop windows outside the current Space.")


TOOL: dict[str, Any] = {
    "name": "list_windows",
    "description": "List layer-0 native windows with exact ids, owners, bounds, and visibility metadata.",
    "args": ListWindowsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("list_windows", arguments, add_session=False)
