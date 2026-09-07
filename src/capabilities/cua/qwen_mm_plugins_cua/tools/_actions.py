"""Shared execution and verification for the narrow model-visible action tools."""

from __future__ import annotations

import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from qwen_mm_plugins_cua.driver import (
    SNAPSHOTS,
    CuaError,
    compare_snapshots,
    convert_point,
    coordinate_space_name,
    driver_result_refused,
    element_center_pixels,
    evaluate_condition,
    get_client,
    observe_target,
    resolve_target,
    snapshot_id_from_token,
    state_content,
)
from shared.content import text_error


class StrictArgs(BaseModel):
    """Reject fields that belong to another action instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class Expectation(StrictArgs):
    condition: Literal["element_present", "element_absent", "value_equals", "state_changed"] = Field(
        description="Post-action condition checked only against the fresh state."
    )
    query: str | None = Field(default=None, description="Text/element selector for presence or value checks.")
    value: str | None = Field(default=None, description="Expected value for value_equals.")


class AppTargetArgs(StrictArgs):
    app: str | None = Field(default=None, description="Application name or bundle id. Use pid when ambiguous.")
    pid: int | None = Field(default=None, gt=0, description="Running application process id.")
    window_id: int | None = Field(default=None, gt=0, description="Exact target window id.")


class SnapshotActionArgs(AppTargetArgs):
    snapshot_id: str | None = Field(
        default=None,
        pattern=r"^s[0-9a-f]{8}$",
        description="Latest state snapshot. Optional when element_token carries the same snapshot id.",
    )
    expect: Expectation | None = Field(default=None, description="Optional condition to verify on fresh state.")


class DeliveredActionArgs(SnapshotActionArgs):
    delivery: Literal["auto", "background", "foreground"] = Field(
        default="auto", description="Input delivery. Auto begins without foregrounding the app."
    )


def require_fields(arguments: dict[str, Any], action: str, *names: str) -> None:
    missing = [name for name in names if arguments.get(name) is None]
    if missing:
        raise CuaError(f"{action} requires {', '.join(missing)}")


def address_payload(
    arguments: dict[str, Any], record, action: str, *, allow_empty: bool = False
) -> tuple[dict[str, Any], str]:
    token = arguments.get("element_token")
    has_point = arguments.get("x") is not None or arguments.get("y") is not None
    if token and has_point:
        raise CuaError("use element_token or x/y, not both")
    if token:
        if snapshot_id_from_token(token) != record.snapshot_id:
            raise CuaError("element_token is stale or does not belong to snapshot_id")
        return {"element_token": token}, "element_token"
    if has_point:
        require_fields(arguments, action, "x", "y")
        x, y = convert_point(record, arguments["x"], arguments["y"])
        return {"x": x, "y": y}, coordinate_space_name()
    if allow_empty:
        return {}, "focused_target"
    raise CuaError(f"{action} requires element_token or x/y")


def _base_payload(arguments: dict[str, Any], target, record, delivery_mode: str, *, delivery: bool = True) -> dict:
    payload: dict[str, Any] = {
        "pid": target.pid,
        "window_id": target.window_id,
        "session": record.session,
    }
    if delivery:
        payload["delivery_mode"] = delivery_mode
    return payload


def build_payload(
    action: str, arguments: dict[str, Any], target, record, delivery_mode: str
) -> tuple[str, dict[str, Any], str, int]:
    if action == "click":
        payload = _base_payload(arguments, target, record, delivery_mode)
        address, address_kind = address_payload(arguments, record, action)
        payload.update(address)
        count = arguments.get("count", 1)
        if count == 2:
            return "double_click", payload, address_kind, 1
        payload["button"] = arguments.get("button", "left")
        if arguments.get("semantic_action") is not None:
            payload["action"] = arguments["semantic_action"]
        if arguments.get("modifiers") is not None:
            payload["modifier"] = arguments["modifiers"]
        return "click", payload, address_kind, 1

    if action == "type_text":
        payload = _base_payload(arguments, target, record, delivery_mode)
        address, address_kind = address_payload(arguments, record, action, allow_empty=True)
        payload.update(address)
        payload.update({"text": arguments["text"], "delay_ms": arguments.get("delay_ms", 30)})
        return "type_text", payload, address_kind, 1

    if action == "press_key":
        payload = _base_payload(arguments, target, record, delivery_mode)
        address, address_kind = address_payload(arguments, record, action, allow_empty=True)
        payload.update(address)
        modifiers = arguments.get("modifiers") or []
        if modifiers:
            payload["keys"] = [*modifiers, arguments["key"]]
            return "hotkey", payload, address_kind, arguments.get("repeat", 1)
        payload["key"] = arguments["key"]
        return "press_key", payload, address_kind, arguments.get("repeat", 1)

    if action == "scroll":
        payload = _base_payload(arguments, target, record, delivery_mode)
        address, address_kind = address_payload(arguments, record, action, allow_empty=True)
        payload.update(address)
        payload.update(
            {
                "direction": arguments["direction"],
                "amount": arguments.get("amount", 3),
                "by": arguments.get("by", "line"),
            }
        )
        return "scroll", payload, address_kind, 1

    if action == "drag":
        if not record.frame_valid:
            raise CuaError("drag requires a proven screenshot frame")
        payload = _base_payload(arguments, target, record, delivery_mode)
        start = convert_point(record, arguments["from_x"], arguments["from_y"])
        end = convert_point(record, arguments["to_x"], arguments["to_y"])
        payload.update(
            {
                "from_x": start[0],
                "from_y": start[1],
                "to_x": end[0],
                "to_y": end[1],
                "duration_ms": arguments.get("duration_ms", 500),
                "steps": arguments.get("steps", 20),
                "button": arguments.get("button", "left"),
            }
        )
        if arguments.get("modifiers") is not None:
            payload["modifier"] = arguments["modifiers"]
        return "drag", payload, coordinate_space_name(), 1

    if action == "set_value":
        payload = _base_payload(arguments, target, record, delivery_mode, delivery=False)
        address, address_kind = address_payload(arguments, record, action)
        if address_kind != "element_token":
            raise CuaError("set_value requires element_token")
        payload.update(address)
        payload["value"] = arguments["value"]
        return "set_value", payload, address_kind, 1

    raise CuaError(f"unsupported action: {action}")


def verification(expect: dict | None, before, after, state: dict) -> dict[str, Any]:
    changes = compare_snapshots(before, after)
    if expect:
        satisfied, detail = evaluate_condition(
            expect["condition"],
            state,
            query=expect.get("query"),
            value=expect.get("value"),
            before=before,
            after=after,
        )
        return {**changes, "condition": expect, "verified": satisfied, "detail": detail}
    return {
        **changes,
        "condition": None,
        "verified": None,
        "detail": "No explicit expectation was supplied; inspect the fresh state before claiming success.",
    }


def _observe_after(client, target, session: str):
    return observe_target(
        client,
        target,
        include_screenshot=True,
        max_elements=600,
        max_depth=20,
        session=session,
    )


def _attempt(
    client,
    tool: str,
    payload: dict,
    address_kind: str,
    *,
    tolerate_driver_error: bool = False,
) -> dict[str, Any]:
    attempt = {
        "tool": tool,
        "delivery_mode": payload.get("delivery_mode"),
        "address": address_kind,
    }
    try:
        result = client.call(tool, payload, timeout=45)
        attempt["driver_result"] = result
        attempt["accepted"] = not driver_result_refused(result)
    except CuaError as exc:
        if not tolerate_driver_error:
            raise
        attempt["driver_error"] = str(exc)
        attempt["accepted"] = False
    return attempt


def _attempt_accepted(attempt: dict[str, Any]) -> bool:
    return attempt.get("accepted") is True


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CuaError(f"global pointer fallback could not prove {name}")
    result = float(value)
    if not math.isfinite(result):
        raise CuaError(f"global pointer fallback received non-finite {name}")
    return result


def _bounds(value: Any, source: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise CuaError(f"global pointer fallback could not prove {source} bounds")
    bounds = {name: _finite_number(value.get(name), f"{source}.{name}") for name in ("x", "y", "width", "height")}
    if bounds["width"] <= 0 or bounds["height"] <= 0:
        raise CuaError(f"global pointer fallback received invalid {source} bounds")
    return bounds


def _require_same_bounds(expected: dict[str, float], actual: dict[str, float]) -> None:
    if any(abs(expected[name] - actual[name]) > 1.0 for name in expected):
        raise CuaError("global pointer fallback refused because the target window moved or resized")


def _require_visible_target(target) -> None:
    if target.window.get("is_on_screen") is not True:
        raise CuaError("global pointer fallback requires an on-screen target window")
    if sys.platform == "darwin" and target.window.get("on_current_space") is not True:
        raise CuaError("global pointer fallback requires the target window on the current Space")


def _validate_global_pointer_request(arguments: dict[str, Any]) -> None:
    if not arguments.get("retry_if_unverified", False) or arguments.get("allow_global_pointer_fallback", True) is False:
        return
    if arguments.get("delivery", "auto") != "auto":
        raise CuaError("global pointer fallback requires delivery='auto'")
    if arguments.get("count", 1) != 1:
        raise CuaError("global pointer fallback supports only a single click")
    if arguments.get("button", "left") != "left" or arguments.get("modifiers"):
        raise CuaError("global pointer fallback supports only an unmodified left click")


def _global_pointer_attempt(client, target, before, current, point) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map a snapshot point to the primary desktop and deliver one real pointer click."""
    if not before.frame_valid or before.width is None or before.height is None:
        raise CuaError("global pointer fallback requires the original proven PNG frame")
    if not current.frame_valid or current.width is None or current.height is None:
        raise CuaError("global pointer fallback requires a fresh proven PNG frame")
    point_x = _finite_number(point[0], "window pixel x")
    point_y = _finite_number(point[1], "window pixel y")
    if not (0 <= point_x < before.width and 0 <= point_y < before.height):
        raise CuaError("global pointer fallback point is outside the original PNG frame")

    observed_bounds = _bounds(current.state.get("window_bounds"), "observed window")
    exact_target = resolve_target(
        client,
        app=None,
        pid=target.pid,
        window_id=target.window_id,
        launch_if_needed=False,
    )
    _require_visible_target(exact_target)
    _require_same_bounds(observed_bounds, _bounds(exact_target.window.get("bounds"), "current window"))

    activation = client.call(
        "bring_to_front",
        {"pid": target.pid, "window_id": target.window_id, "session": before.session},
        timeout=30,
    )
    exact_effect = activation.get("exact_window_effect")
    if (
        activation.get("activated") is not True
        or not isinstance(exact_effect, dict)
        or exact_effect.get("verified") is not True
    ):
        raise CuaError("global pointer fallback could not verify the exact target window was foregrounded")

    exact_target = resolve_target(
        client,
        app=None,
        pid=target.pid,
        window_id=target.window_id,
        launch_if_needed=False,
    )
    _require_visible_target(exact_target)
    _require_same_bounds(observed_bounds, _bounds(exact_target.window.get("bounds"), "foreground window"))

    screen_x = observed_bounds["x"] + point_x * observed_bounds["width"] / before.width
    screen_y = observed_bounds["y"] + point_y * observed_bounds["height"] / before.height
    with tempfile.TemporaryDirectory(prefix="qwen-mm-cua-") as temp_dir:
        screenshot_path = Path(temp_dir) / "desktop.png"
        desktop = client.call(
            "get_desktop_state",
            {"screenshot_out_file": str(screenshot_path), "session": before.session},
            timeout=45,
        )
        screen_width = _finite_number(desktop.get("screen_width"), "desktop logical width")
        screen_height = _finite_number(desktop.get("screen_height"), "desktop logical height")
        desktop_width = _finite_number(desktop.get("screenshot_width"), "desktop PNG width")
        desktop_height = _finite_number(desktop.get("screenshot_height"), "desktop PNG height")
        if min(screen_width, screen_height, desktop_width, desktop_height) <= 0:
            raise CuaError("global pointer fallback received invalid desktop dimensions")
        if not (0 <= screen_x < screen_width and 0 <= screen_y < screen_height):
            raise CuaError("global pointer fallback supports only targets proven inside the primary screen")
        desktop_x = round(screen_x * desktop_width / screen_width)
        desktop_y = round(screen_y * desktop_height / screen_height)

        move = client.call(
            "move_cursor",
            {"scope": "desktop", "x": desktop_x, "y": desktop_y, "session": before.session},
            timeout=30,
        )
        if move.get("route") != "global_input":
            raise CuaError("global pointer fallback could not verify the cursor used global_input")
        time.sleep(0.1)
        cursor = client.call("get_cursor_position", {"session": before.session}, timeout=15)
        cursor_x = _finite_number(cursor.get("x"), "cursor x")
        cursor_y = _finite_number(cursor.get("y"), "cursor y")
        if abs(cursor_x - screen_x) > 2.5 or abs(cursor_y - screen_y) > 2.5:
            raise CuaError("global pointer fallback refused because cursor readback missed the target")

        result = client.call(
            "click",
            {
                "scope": "desktop",
                "x": desktop_x,
                "y": desktop_y,
                "button": "left",
                "session": before.session,
            },
            timeout=30,
        )

    route_verified = result.get("route") == "global_input"
    attempt = {
        "tool": "click",
        "delivery_mode": "foreground",
        "address": "desktop_global_pointer",
        "driver_result": result,
        "accepted": route_verified and not driver_result_refused(result),
    }
    metadata = {
        "attempted": True,
        "route_verified": route_verified,
        "activation_verified": True,
        "cursor_readback": {"x": cursor_x, "y": cursor_y},
        "mapping": {
            "window_pixel": {"x": point_x, "y": point_y},
            "screen_logical": {"x": screen_x, "y": screen_y},
            "desktop_pixel": {"x": desktop_x, "y": desktop_y},
        },
    }
    if not route_verified:
        metadata["refused"] = "desktop click did not report route=global_input"
    return attempt, metadata


def _execute_locked(action: str, arguments: dict[str, Any], client, target) -> list[dict[str, str]]:
    supplied_snapshot = arguments.get("snapshot_id") or snapshot_id_from_token(arguments.get("element_token"))
    before = SNAPSHOTS.consume(target, supplied_snapshot)
    delivery = arguments.get("delivery", "auto")
    initial_mode = "background" if delivery == "auto" else delivery
    tool, payload, address_kind, repeat = build_payload(action, arguments, target, before, initial_mode)
    tolerate_click_error = (
        delivery == "auto"
        and action == "click"
        and arguments.get("count", 1) == 1
        and arguments.get("retry_if_unverified", False)
    )
    attempts = [
        _attempt(
            client,
            tool,
            payload,
            address_kind,
            tolerate_driver_error=tolerate_click_error,
        )
        for _ in range(repeat)
    ]
    state, after = _observe_after(client, target, before.session)
    checked = verification(arguments.get("expect"), before, after, state)
    pointer_fallback: dict[str, Any] | None = None

    needs_retry = checked.get("verified") is False if arguments.get("expect") else not checked["state_changed"]
    can_retry = (
        delivery == "auto"
        and action == "click"
        and arguments.get("count", 1) == 1
        and arguments.get("retry_if_unverified", False)
        and needs_retry
    )
    if can_retry:
        point = None
        if arguments.get("element_token"):
            point = element_center_pixels(before, arguments["element_token"])
        elif arguments.get("x") is not None and arguments.get("y") is not None:
            point = convert_point(before, arguments["x"], arguments["y"])
        if point is not None:
            pixel_payload = {
                "pid": target.pid,
                "window_id": target.window_id,
                "session": before.session,
                "delivery_mode": "background",
                "x": point[0],
                "y": point[1],
                "button": arguments.get("button", "left"),
            }
            if arguments.get("element_token"):
                attempts.append(
                    _attempt(
                        client,
                        "click",
                        pixel_payload,
                        "pixel_fallback",
                        tolerate_driver_error=True,
                    )
                )
                state, after = _observe_after(client, target, before.session)
                checked = verification(arguments.get("expect"), before, after, state)
            needs_foreground = (
                checked.get("verified") is False if arguments.get("expect") else not checked["state_changed"]
            )
            if needs_foreground:
                pixel_payload["delivery_mode"] = "foreground"
                attempts.append(
                    _attempt(
                        client,
                        "click",
                        pixel_payload,
                        "pixel_fallback",
                        tolerate_driver_error=True,
                    )
                )
                state, after = _observe_after(client, target, before.session)
                checked = verification(arguments.get("expect"), before, after, state)

            needs_global = checked.get("verified") is False if arguments.get("expect") else not checked["state_changed"]
            if arguments.get("allow_global_pointer_fallback", True):
                pointer_fallback = {"requested": True, "attempted": False}
                if not needs_global:
                    pointer_fallback["refused"] = "prior click already satisfied verification"
                elif checked["state_changed"]:
                    pointer_fallback["refused"] = (
                        "prior click changed state; refusing to reuse its old coordinate for global input"
                    )
                else:
                    try:
                        pointer_attempt, pointer_metadata = _global_pointer_attempt(
                            client, target, before, after, point
                        )
                        attempts.append(pointer_attempt)
                        pointer_fallback.update(pointer_metadata)
                        state, after = _observe_after(client, target, before.session)
                        checked = verification(arguments.get("expect"), before, after, state)
                    except CuaError as exc:
                        pointer_fallback["refused"] = str(exc)

    driver_accepted = any(_attempt_accepted(attempt) for attempt in attempts)
    if arguments.get("expect"):
        action_ok = driver_accepted and checked.get("verified") is True
    elif arguments.get("retry_if_unverified", False):
        action_ok = driver_accepted and checked["state_changed"] is True
    else:
        action_ok = all(_attempt_accepted(attempt) for attempt in attempts)
    return state_content(
        target,
        state,
        after,
        ok=action_ok,
        extra={
            "interaction": {
                "action": action,
                "driver_accepted": driver_accepted,
                "attempts": attempts,
                "verification": checked,
                "global_pointer_fallback": pointer_fallback,
                "foreground_retry_available": (
                    delivery == "auto"
                    and action == "click"
                    and arguments.get("count", 1) == 1
                    and not arguments.get("retry_if_unverified", False)
                    and not checked["state_changed"]
                ),
            }
        },
    )


def execute(action: str, arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Run one narrow action and always return a newly observed app state."""
    try:
        _validate_global_pointer_request(arguments)
        client = get_client()
        target = resolve_target(
            client,
            app=arguments.get("app"),
            pid=arguments.get("pid"),
            window_id=arguments.get("window_id"),
            launch_if_needed=False,
        )
        with SNAPSHOTS.transaction(target):
            return _execute_locked(action, arguments, client, target)
    except (CuaError, ValueError, KeyError) as exc:
        return text_error(str(exc))
