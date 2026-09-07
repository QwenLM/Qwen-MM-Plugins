"""MCP tool: bounded polling on fresh Cua Driver snapshots."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.driver import (
    DEFAULT_SESSION,
    SCREENSHOT_KEY,
    SNAPSHOTS,
    CuaError,
    evaluate_condition,
    get_client,
    observe_target,
    resolve_target,
    state_content,
)
from qwen_mm_plugins_cua.tools._actions import StrictArgs
from shared.content import text_error


class WaitArgs(StrictArgs):
    condition: Literal["element_present", "element_absent", "value_equals", "state_changed", "stable"]
    app: str | None = Field(default=None, description="Application name or bundle id.")
    pid: int | None = Field(default=None, gt=0, description="Running application process id.")
    window_id: int | None = Field(default=None, gt=0, description="Exact target window id.")
    snapshot_id: str | None = Field(
        default=None,
        pattern=r"^s[0-9a-f]{8}$",
        description="Baseline snapshot; required for state_changed and recommended for all waits.",
    )
    query: str | None = Field(default=None, description="Text/element selector for present, absent, or value checks.")
    value: str | None = Field(default=None, description="Expected value for value_equals.")
    timeout_seconds: float = Field(default=10, ge=0.1, le=30, description="Maximum bounded wait.")
    poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=5, description="Polling interval.")
    stable_for_seconds: float = Field(default=1, ge=0.2, le=10, description="Unchanged duration for condition=stable.")
    use_screenshot: bool = Field(
        default=False, description="Compare screenshots while polling; use for visual-only/custom surfaces."
    )
    include_screenshot: bool = Field(default=True, description="Attach a fresh final screenshot.")
    max_elements: int = Field(default=600, ge=1, le=2000, description="Maximum AX nodes per poll.")
    max_depth: int = Field(default=20, ge=1, le=25, description="Maximum AX depth per poll.")
    session: str = Field(
        default=DEFAULT_SESSION, min_length=1, max_length=80, description="Stable public session label."
    )

    @model_validator(mode="after")
    def validate_condition_inputs(self):
        if self.condition in {"element_present", "element_absent"} and not self.query:
            raise ValueError(f"condition={self.condition} requires query")
        if self.condition == "value_equals" and self.value is None:
            raise ValueError("condition=value_equals requires value")
        if self.condition == "state_changed" and not self.snapshot_id:
            raise ValueError("condition=state_changed requires snapshot_id")
        return self


TOOL: dict[str, Any] = {
    "name": "wait",
    "description": (
        "Wait up to 30 seconds for a fresh-state condition, then return the final screenshot and AX "
        "state. Polling invalidates prior element tokens; use only the snapshot returned by this call."
    ),
    "args": WaitArgs,
}


def _wait_locked(arguments: dict[str, Any], client, target) -> list[dict[str, str]]:
    condition = arguments["condition"]
    if condition == "state_changed" and not arguments.get("snapshot_id"):
        raise CuaError("condition=state_changed requires snapshot_id from get_app_state")
    baseline = None
    if arguments.get("snapshot_id"):
        baseline = SNAPSHOTS.consume(target, arguments["snapshot_id"])
    elif current := SNAPSHOTS.current(target):
        SNAPSHOTS.consume(target, current.snapshot_id)
    session = baseline.session if baseline is not None else arguments.get("session", DEFAULT_SESSION)
    timeout = arguments.get("timeout_seconds", 10)
    interval = arguments.get("poll_interval_seconds", 0.5)
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    stable_since = None
    previous = baseline
    satisfied = False
    detail = "condition not evaluated"
    state = None
    record = None
    capture_screenshot = arguments.get("include_screenshot", True) or arguments.get("use_screenshot", False)

    while True:
        state, record = observe_target(
            client,
            target,
            include_screenshot=capture_screenshot,
            max_elements=arguments.get("max_elements", 600),
            max_depth=arguments.get("max_depth", 20),
            session=session,
        )
        if condition == "stable":
            unchanged = previous is not None and previous.ax_hash == record.ax_hash
            if arguments.get("use_screenshot", False):
                unchanged = unchanged and previous.visual_hash == record.visual_hash
            if unchanged:
                stable_since = stable_since or time.monotonic()
                satisfied = time.monotonic() - stable_since >= arguments.get("stable_for_seconds", 1)
            else:
                stable_since = None
            detail = "state is stable" if satisfied else "state has not remained stable long enough"
            previous = record
        else:
            satisfied, detail = evaluate_condition(
                condition,
                state,
                query=arguments.get("query"),
                value=arguments.get("value"),
                before=baseline,
                after=record,
            )
        if satisfied or time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    assert state is not None and record is not None
    if not arguments.get("include_screenshot", True):
        state = dict(state)
        state.pop(SCREENSHOT_KEY, None)
        record = replace(record, frame_valid=False, width=None, height=None)
        SNAPSHOTS.put(record)
    return state_content(
        target,
        state,
        record,
        ok=satisfied,
        extra={
            "wait_result": {
                "condition": condition,
                "satisfied": satisfied,
                "timed_out": not satisfied,
                "detail": detail,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        },
    )


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    try:
        client = get_client()
        target = resolve_target(
            client,
            app=arguments.get("app"),
            pid=arguments.get("pid"),
            window_id=arguments.get("window_id"),
            launch_if_needed=False,
        )
        with SNAPSHOTS.transaction(target):
            return _wait_locked(arguments, client, target)
    except (CuaError, ValueError, KeyError) as exc:
        return text_error(str(exc))
