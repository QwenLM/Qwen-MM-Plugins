"""Return a fresh primary-display screenshot."""

from __future__ import annotations

from typing import Any

from qwen_mm_plugins_cua.native_tools._desktop import desktop_content, observe_desktop
from qwen_mm_plugins_cua.tools._actions import StrictArgs
from shared.content import text_error


class GetDesktopStateArgs(StrictArgs):
    pass


TOOL: dict[str, Any] = {
    "name": "get_desktop_state",
    "description": (
        "Return a fresh screenshot of the primary display with its authoritative coordinate_space. "
        "Call before every native action; arbitrary display selection is not supported by Driver 0.20."
    ),
    "args": GetDesktopStateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    try:
        state, snapshot = observe_desktop()
        return desktop_content(state, snapshot)
    except (RuntimeError, ValueError, KeyError) as exc:
        return text_error(str(exc))
