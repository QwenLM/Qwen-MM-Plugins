"""Shared typed-browser models and Driver result adaptation."""

from __future__ import annotations

import json
from typing import Any

from qwen_mm_plugins_cua.driver import (
    DEFAULT_SESSION,
    SCREENSHOT_KEY,
    CuaError,
    driver_result_refused,
    get_client,
)
from qwen_mm_plugins_cua.tools._actions import StrictArgs
from shared.content import image, text, text_error


class BrowserTargetArgs(StrictArgs):
    target_id: str
    tab_id: str
    session: str = DEFAULT_SESSION


def call_driver_tool(
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout: float = 45,
    add_session: bool = True,
) -> list[dict[str, str]]:
    try:
        payload = dict(arguments)
        if add_session:
            payload.setdefault("session", DEFAULT_SESSION)
        result = get_client().call(tool, payload, timeout=timeout)
        screenshot = result.get(SCREENSHOT_KEY)
        public = {key: value for key, value in result.items() if key != SCREENSHOT_KEY}
        refused = driver_result_refused(public)
        driver_ok = public.pop("ok", True)
        blocks = [text(json.dumps({"ok": bool(driver_ok) and not refused, **public}, ensure_ascii=False, indent=2))]
        if isinstance(screenshot, str):
            blocks.append(image(screenshot, result.get("screenshot_mime_type", "image/png")))
        return blocks
    except (CuaError, ValueError, KeyError) as exc:
        return text_error(str(exc))
