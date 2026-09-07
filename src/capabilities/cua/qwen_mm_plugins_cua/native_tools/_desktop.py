"""Snapshot-bound primary-display observation and global input delivery."""

from __future__ import annotations

import json
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from qwen_mm_plugins_cua.driver import (
    DEFAULT_SESSION,
    SCREENSHOT_KEY,
    CuaError,
    _perceptual_hash,
    coordinate_contract,
    coordinate_instruction,
    coordinate_to_pixel,
    driver_result_refused,
    get_client,
)
from shared.content import image, text, text_error

DESKTOP_TARGET = {"kind": "desktop", "display_id": "primary"}


@dataclass(frozen=True)
class DesktopSnapshot:
    snapshot_id: str
    width: int
    height: int
    visual_hash: str | None


class DesktopSnapshotStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._transaction_lock = threading.RLock()
        self._current: DesktopSnapshot | None = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._transaction_lock:
            yield

    def put(self, snapshot: DesktopSnapshot) -> None:
        with self._lock:
            self._current = snapshot

    def current(self) -> DesktopSnapshot | None:
        with self._lock:
            return self._current

    def consume(self, snapshot_id: str | None) -> DesktopSnapshot:
        with self._lock:
            current = self._current
            if current is None:
                raise CuaError("no current desktop snapshot; call get_desktop_state first")
            if not snapshot_id:
                raise CuaError("snapshot_id is required; copy it from the latest get_desktop_state result")
            if snapshot_id != current.snapshot_id:
                raise CuaError(
                    f"stale snapshot_id {snapshot_id!r}; the current desktop snapshot is "
                    f"{current.snapshot_id!r}. Re-observe before acting."
                )
            self._current = None
            return current

    def clear(self) -> None:
        with self._lock:
            self._current = None


DESKTOP_SNAPSHOTS = DesktopSnapshotStore()


def observe_desktop() -> tuple[dict[str, Any], DesktopSnapshot]:
    with DESKTOP_SNAPSHOTS.transaction():
        state = get_client().call("get_desktop_state", {"session": DEFAULT_SESSION}, timeout=45)
        screenshot = state.get(SCREENSHOT_KEY)
        width = state.get("screenshot_width")
        height = state.get("screenshot_height")
        if not isinstance(screenshot, str):
            raise CuaError("get_desktop_state returned no embedded PNG")
        if not isinstance(width, int) or width <= 0 or not isinstance(height, int) or height <= 0:
            raise CuaError("get_desktop_state returned invalid screenshot dimensions")
        snapshot = DesktopSnapshot(
            snapshot_id=f"s{secrets.token_hex(4)}",
            width=width,
            height=height,
            visual_hash=_perceptual_hash(screenshot),
        )
        DESKTOP_SNAPSHOTS.put(snapshot)
        return state, snapshot


def desktop_content(
    state: dict[str, Any],
    snapshot: DesktopSnapshot,
    *,
    extra: dict[str, Any] | None = None,
    ok: bool = True,
) -> list[dict[str, str]]:
    screenshot = state[SCREENSHOT_KEY]
    public_state = {key: value for key, value in state.items() if key != SCREENSHOT_KEY}
    public_state["snapshot_id"] = snapshot.snapshot_id
    coordinate_space = coordinate_contract(snapshot.width, snapshot.height, snapshot.snapshot_id)
    payload: dict[str, Any] = {
        "ok": ok,
        "target": DESKTOP_TARGET,
        "image_metadata": {
            "format": "png",
            "width": snapshot.width,
            "height": snapshot.height,
            "snapshot_binding": snapshot.snapshot_id,
            "authoritative_for_coordinates": True,
        },
        "coordinate_space": coordinate_space,
        "state": public_state,
    }
    if extra:
        payload.update(extra)
    return [
        text(json.dumps(payload, ensure_ascii=False, indent=2)),
        text(coordinate_instruction(snapshot.width, snapshot.height, snapshot.snapshot_id)),
        image(screenshot, state.get("screenshot_mime_type", "image/png")),
    ]


def desktop_point(snapshot: DesktopSnapshot, x: float, y: float) -> tuple[float, float]:
    return (
        coordinate_to_pixel(x, snapshot.width, "x"),
        coordinate_to_pixel(y, snapshot.height, "y"),
    )


def visual_comparison(before: DesktopSnapshot, after: DesktopSnapshot) -> dict[str, bool | float | None]:
    if before.visual_hash is None or after.visual_hash is None:
        return {"visual_changed": None, "visual_change_score": None}
    distance = (int(before.visual_hash, 16) ^ int(after.visual_hash, 16)).bit_count()
    return {
        "visual_changed": distance > 20,
        "visual_change_score": round(distance / 1024, 4),
    }


def _action_payload(action: str, arguments: dict[str, Any], before: DesktopSnapshot) -> tuple[str, dict, int]:
    payload: dict[str, Any] = {"target": DESKTOP_TARGET, "session": DEFAULT_SESSION}
    repeat = 1
    if action in {"click", "move_cursor", "scroll"}:
        x, y = desktop_point(before, arguments["x"], arguments["y"])
        payload.update({"x": x, "y": y})
    if action == "click":
        payload.update({"button": arguments.get("button", "left"), "count": arguments.get("count", 1)})
        if arguments.get("modifiers"):
            payload["modifier"] = arguments["modifiers"]
        return "click", payload, repeat
    if action == "move_cursor":
        return "move_cursor", payload, repeat
    if action == "drag":
        from_x, from_y = desktop_point(before, arguments["from_x"], arguments["from_y"])
        to_x, to_y = desktop_point(before, arguments["to_x"], arguments["to_y"])
        payload.update(
            {
                "from_x": from_x,
                "from_y": from_y,
                "to_x": to_x,
                "to_y": to_y,
                "duration_ms": arguments.get("duration_ms", 500),
                "steps": arguments.get("steps", 20),
                "button": arguments.get("button", "left"),
            }
        )
        if arguments.get("modifiers"):
            payload["modifier"] = arguments["modifiers"]
        return "drag", payload, repeat
    if action == "scroll":
        payload.update(
            {
                "direction": arguments["direction"],
                "amount": arguments.get("amount", 3),
                "by": arguments.get("by", "line"),
            }
        )
        return "scroll", payload, repeat
    if action == "press_key":
        modifiers = arguments.get("modifiers") or []
        repeat = arguments.get("repeat", 1)
        if modifiers:
            payload["keys"] = [*modifiers, arguments["key"]]
            return "hotkey", payload, repeat
        payload["key"] = arguments["key"]
        return "press_key", payload, repeat
    if action == "type_text":
        payload.update({"text": arguments["text"], "delay_ms": arguments.get("delay_ms", 30)})
        return "type_text", payload, repeat
    raise CuaError(f"unsupported native action: {action}")


def execute_desktop_action(action: str, arguments: dict[str, Any]) -> list[dict[str, str]]:
    try:
        with DESKTOP_SNAPSHOTS.transaction():
            before = DESKTOP_SNAPSHOTS.consume(arguments.get("snapshot_id"))
            tool, payload, repeat = _action_payload(action, arguments, before)
            results = [get_client().call(tool, payload, timeout=45) for _ in range(repeat)]
            state, after = observe_desktop()
            comparison = visual_comparison(before, after)
            expected = arguments.get("expect") is not None
            verification = {
                **comparison,
                "condition": arguments.get("expect"),
                "verified": comparison["visual_changed"] if expected else None,
                "detail": (
                    "fresh primary-display screenshot changed"
                    if comparison["visual_changed"] is True
                    else "fresh primary-display screenshot is unchanged or change could not be measured"
                ),
            }
            driver_accepted = all(not driver_result_refused(result) for result in results)
            action_ok = driver_accepted and (not expected or verification["verified"] is True)
            return desktop_content(
                state,
                after,
                ok=action_ok,
                extra={
                    "interaction": {
                        "action": action,
                        "driver_accepted": driver_accepted,
                        "driver_results": results,
                        "verification": verification,
                    }
                },
            )
    except (CuaError, ValueError, KeyError) as exc:
        return text_error(str(exc))


def wait_for_visual(arguments: dict[str, Any]) -> list[dict[str, str]]:
    try:
        with DESKTOP_SNAPSHOTS.transaction():
            condition = arguments["condition"]
            if condition == "state_changed" and not arguments.get("snapshot_id"):
                raise CuaError("condition=state_changed requires snapshot_id from get_desktop_state")
            baseline = None
            if arguments.get("snapshot_id"):
                baseline = DESKTOP_SNAPSHOTS.consume(arguments["snapshot_id"])
            elif current := DESKTOP_SNAPSHOTS.current():
                DESKTOP_SNAPSHOTS.consume(current.snapshot_id)
            timeout = arguments.get("timeout_seconds", 10)
            interval = arguments.get("poll_interval_seconds", 0.5)
            deadline = time.monotonic() + timeout
            started = time.monotonic()
            previous = baseline
            stable_since = None
            satisfied = False
            detail = "condition not evaluated"
            state = None
            snapshot = None
            while True:
                state, snapshot = observe_desktop()
                if condition == "state_changed":
                    changed = visual_comparison(baseline, snapshot)["visual_changed"]
                    satisfied = changed is True
                    detail = "desktop changed" if satisfied else "desktop is unchanged"
                else:
                    unchanged = (
                        previous is not None and visual_comparison(previous, snapshot)["visual_changed"] is False
                    )
                    if unchanged:
                        stable_since = stable_since or time.monotonic()
                        satisfied = time.monotonic() - stable_since >= arguments.get("stable_for_seconds", 1)
                    else:
                        stable_since = None
                    detail = "desktop is stable" if satisfied else "desktop has not remained stable long enough"
                    previous = snapshot
                if satisfied or time.monotonic() >= deadline:
                    break
                time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

            assert state is not None and snapshot is not None
            return desktop_content(
                state,
                snapshot,
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
    except (CuaError, ValueError, KeyError) as exc:
        return text_error(str(exc))


def reset_desktop_runtime_for_tests() -> None:
    DESKTOP_SNAPSHOTS.clear()
