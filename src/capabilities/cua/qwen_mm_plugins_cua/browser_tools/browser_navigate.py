"""Navigate one exact browser tab."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from qwen_mm_plugins_cua.browser_tools._shared import BrowserTargetArgs, call_driver_tool


class BrowserNavigateArgs(BrowserTargetArgs):
    url: str = Field(min_length=1, description="Destination URL using http, https, or about.")


TOOL: dict[str, Any] = {
    "name": "browser_navigate",
    "description": "Navigate one exactly bound tab. Navigation invalidates every older ref for that tab.",
    "args": BrowserNavigateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_navigate", arguments)
