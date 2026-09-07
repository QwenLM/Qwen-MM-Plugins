"""Bind an exact browser window or inspect one exact tab."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.browser_tools._shared import call_driver_tool
from qwen_mm_plugins_cua.driver import DEFAULT_SESSION
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class GetBrowserStateArgs(StrictArgs):
    pid: int | None = Field(default=None, gt=0, description="Native browser pid for bind mode.")
    window_id: int | None = Field(default=None, gt=0, description="Native browser window id for bind mode.")
    target_id: str | None = Field(default=None, description="Opaque exact browser target id for snapshot mode.")
    tab_id: str | None = Field(default=None, description="Opaque exact tab id for snapshot mode.")
    snapshot_format: Literal["dom_refs_v1", "semantic_v2"] = "semantic_v2"
    include_screenshot: bool = Field(default=False, description="Capture the exact tab viewport through CDP.")
    query: str | None = Field(default=None, description="Read-only semantic match.")
    scope_ref: str | None = Field(default=None, description="Observe only this current semantic subtree.")
    continuation: str | None = Field(default=None, description="Opaque continuation from semantic_v2.")
    session: str = DEFAULT_SESSION

    @model_validator(mode="after")
    def validate_mode(self):
        bind = self.pid is not None or self.window_id is not None
        snapshot = self.target_id is not None or self.tab_id is not None
        if bind == snapshot:
            raise ValueError("use exactly one mode: pid+window_id (bind) or target_id+tab_id (snapshot)")
        if bind and (self.pid is None or self.window_id is None):
            raise ValueError("bind mode requires pid and window_id")
        if snapshot and (self.target_id is None or self.tab_id is None):
            raise ValueError("snapshot mode requires target_id and tab_id")
        return self


TOOL: dict[str, Any] = {
    "name": "get_browser_state",
    "description": (
        "Bind one exact native browser window or return a typed DOM/semantic snapshot for one exact tab. "
        "Every newer snapshot invalidates older page refs."
    ),
    "args": GetBrowserStateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("get_browser_state", arguments)
