"""Trigger a typed browser download into one approved directory."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from qwen_mm_plugins_cua.browser_tools._shared import BrowserTargetArgs, call_driver_tool


class BrowserDownloadArgs(BrowserTargetArgs):
    ref: str = Field(description="Current page ref whose activation initiates the download.")
    destination_root: str = Field(description="Absolute existing canonical directory approved for the download.")


TOOL: dict[str, Any] = {
    "name": "browser_download",
    "description": "Trigger one exact-ref download into an explicitly approved existing directory.",
    "args": BrowserDownloadArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_download", arguments, timeout=120)
