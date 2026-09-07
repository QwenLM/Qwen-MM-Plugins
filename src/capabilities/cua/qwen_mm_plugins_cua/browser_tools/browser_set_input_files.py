"""Assign explicit local files to one current file-input ref."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from qwen_mm_plugins_cua.browser_tools._shared import BrowserTargetArgs, call_driver_tool


class BrowserSetInputFilesArgs(BrowserTargetArgs):
    ref: str = Field(description="Current file-input page ref from get_browser_state.")
    files: list[str] = Field(min_length=1, max_length=32, description="Absolute local regular-file paths.")


TOOL: dict[str, Any] = {
    "name": "browser_set_input_files",
    "description": "Assign explicit absolute local files to one exact live file-input ref without a native picker.",
    "args": BrowserSetInputFilesArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_set_input_files", arguments)
