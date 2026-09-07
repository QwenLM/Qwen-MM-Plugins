"""MCP tool: resolve an app's ordinary window and return a fresh grounded snapshot."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from qwen_mm_plugins_cua.driver import DEFAULT_SESSION, CuaError, get_state, state_content
from qwen_mm_plugins_cua.tools._actions import StrictArgs
from shared.content import text_error


class GetAppStateArgs(StrictArgs):
    app: str | None = Field(default=None, description="Application name or bundle id. Use pid when ambiguous.")
    pid: int | None = Field(default=None, gt=0, description="Running application process id.")
    window_id: int | None = Field(default=None, gt=0, description="Exact window id; otherwise select the main window.")
    launch_if_needed: bool = Field(default=True, description="Launch an installed app when it is not running.")
    query: str | None = Field(default=None, description="Optional case-insensitive AX-tree projection.")
    include_screenshot: bool = Field(default=True, description="Return the current PNG with the AX elements.")
    max_elements: int = Field(default=600, ge=1, le=2000, description="Maximum AX nodes returned.")
    max_depth: int = Field(default=20, ge=1, le=25, description="Maximum AX-tree depth returned.")
    session: str = Field(
        default=DEFAULT_SESSION, min_length=1, max_length=80, description="Stable public session label."
    )


TOOL: dict[str, Any] = {
    "name": "get_app_state",
    "description": (
        "Return a fresh screenshot plus accessibility state for one exact app window. Call before "
        "an action and use only the returned snapshot. The configured screenshot coordinates are bound to the "
        "returned snapshot_id; pathological narrow helper surfaces are rejected."
    ),
    "args": GetAppStateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    try:
        target, state, record = get_state(
            app=arguments.get("app"),
            pid=arguments.get("pid"),
            window_id=arguments.get("window_id"),
            launch_if_needed=arguments.get("launch_if_needed", True),
            query=arguments.get("query"),
            include_screenshot=arguments.get("include_screenshot", True),
            max_elements=arguments.get("max_elements", 600),
            max_depth=arguments.get("max_depth", 20),
            session=arguments.get("session", DEFAULT_SESSION),
        )
        return state_content(target, state, record)
    except (CuaError, ValueError) as exc:
        return text_error(str(exc))
