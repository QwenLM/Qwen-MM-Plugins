"""Offline coverage for the narrow Cua Driver adapter."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import qwen_mm_plugins_cua
from qwen_mm_plugins_cua import driver
from qwen_mm_plugins_cua.browser_tools import get_browser_state as browser_state_tool
from qwen_mm_plugins_cua.native_tools import _desktop as desktop_runtime
from qwen_mm_plugins_cua.native_tools import click as native_click
from qwen_mm_plugins_cua.native_tools import get_desktop_state as native_desktop_state
from qwen_mm_plugins_cua.native_tools import press_key as native_press_key
from qwen_mm_plugins_cua.native_tools import type_text as native_type_text
from qwen_mm_plugins_cua.native_tools import wait as native_wait
from qwen_mm_plugins_cua.tools import click, get_app_state, list_apps, press_key, set_value, type_text, wait

MAIN_WINDOW = {
    "app_name": "Music",
    "bounds": {"x": 100.0, "y": 100.0, "width": 800.0, "height": 600.0},
    "is_on_screen": True,
    "layer": 0,
    "on_current_space": True,
    "pid": 123,
    "title": "Music",
    "window_id": 42,
    "z_index": 5,
}
NARROW_WINDOW = {
    "app_name": "Music",
    "bounds": {"x": 0.0, "y": 0.0, "width": 1280.0, "height": 35.0},
    "is_on_screen": True,
    "layer": 0,
    "on_current_space": True,
    "pid": 123,
    "title": "",
    "window_id": 7,
    "z_index": 0,
}


def _state(snapshot: int, label: str = "Search", screenshot: str = "same-image") -> dict:
    snapshot_id = f"s{snapshot:08x}"
    return {
        "element_count": 2,
        "elements": [
            {
                "depth": 0,
                "element_index": 0,
                "element_token": f"{snapshot_id}:0",
                "frame": {"x": 100.0, "y": 100.0, "w": 800.0, "h": 600.0},
                "label": "Music",
                "role": "AXWindow",
            },
            {
                "depth": 1,
                "element_index": 1,
                "element_token": f"{snapshot_id}:1",
                "frame": {"x": 110.0, "y": 120.0, "w": 40.0, "h": 20.0},
                "label": label,
                "role": "AXCell",
            },
        ],
        "pid": 123,
        "screenshot_frame_valid": True,
        "screenshot_height": 1200,
        "screenshot_mime_type": "image/png",
        "screenshot_png_b64": screenshot,
        "screenshot_scale": 2.0,
        "screenshot_width": 1600,
        "snapshot_id": snapshot_id,
        "tree_markdown": f"Music\n  AXCell {label}",
        "window_bounds": MAIN_WINDOW["bounds"],
        "window_id": 42,
    }


def _desktop_state(screenshot: str = "desktop-image") -> dict:
    return {
        "screen_width": 800,
        "screen_height": 600,
        "screenshot_height": 1200,
        "screenshot_mime_type": "image/png",
        "screenshot_png_b64": screenshot,
        "screenshot_scale": 2.0,
        "screenshot_width": 1600,
    }


class FakeClient:
    def __init__(self, states: list[dict]) -> None:
        self.states = list(states)
        self.calls: list[tuple[str, dict]] = []
        self.cursor = (0.0, 0.0)

    def call(self, tool: str, arguments: dict | None = None, *, timeout=None) -> dict:
        args = arguments or {}
        self.calls.append((tool, dict(args)))
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "active": False,
                        "bundle_id": "com.apple.Music",
                        "name": "Music",
                        "pid": 123,
                        "running": True,
                    }
                ]
            }
        if tool == "list_windows":
            return {"windows": [NARROW_WINDOW, MAIN_WINDOW]}
        if tool == "get_window_state":
            assert self.states, "test did not provide enough fresh states"
            return self.states.pop(0)
        if tool == "bring_to_front":
            return {"activated": True, "exact_window_effect": {"verified": True}}
        if tool == "get_desktop_state":
            return {
                "screen_width": 1512,
                "screen_height": 982,
                "screenshot_width": 3024,
                "screenshot_height": 1964,
            }
        if tool == "move_cursor":
            self.cursor = (args["x"] / 2, args["y"] / 2)
            return {"route": "global_input"}
        if tool == "get_cursor_position":
            return {"x": self.cursor[0], "y": self.cursor[1]}
        if tool in {"click", "double_click", "type_text", "press_key", "hotkey", "scroll", "drag", "set_value"}:
            if tool == "click" and args.get("scope") == "desktop":
                return {"effect": "unverifiable", "route": "global_input", "tool": tool}
            return {"effect": "unverifiable", "tool": tool}
        if tool == "launch_app":
            return {"bundle_id": "com.apple.Music", "name": "Music", "pid": 123}
        raise AssertionError(f"unexpected driver tool: {tool}")


class TransientStateClient(FakeClient):
    def call(self, tool: str, arguments: dict | None = None, *, timeout=None) -> dict:
        if tool == "get_window_state" and self.states and self.states[0].get("status") == "refused":
            self.calls.append((tool, dict(arguments or {})))
            return self.states.pop(0)
        return super().call(tool, arguments, timeout=timeout)


class AxClickFailureClient(FakeClient):
    def call(self, tool: str, arguments: dict | None = None, *, timeout=None) -> dict:
        args = arguments or {}
        if tool == "click" and args.get("element_token"):
            self.calls.append((tool, dict(args)))
            raise driver.CuaError("AX action failed")
        return super().call(tool, arguments, timeout=timeout)


@pytest.fixture(autouse=True)
def _clean_runtime(monkeypatch):
    driver.reset_runtime_for_tests()
    desktop_runtime.reset_desktop_runtime_for_tests()
    yield
    driver.reset_runtime_for_tests()
    desktop_runtime.reset_desktop_runtime_for_tests()


def _install_fake(monkeypatch, *states: dict) -> FakeClient:
    fake = FakeClient(list(states))
    monkeypatch.setattr(driver, "_CLIENT", fake)
    return fake


def _payload(blocks: list[dict]) -> dict:
    assert blocks[0]["type"] == "text"
    assert not blocks[0]["text"].startswith("Error:")
    return json.loads(blocks[0]["text"])


def test_registry_exposes_only_nine_narrow_tools():
    assert [spec.name for spec in qwen_mm_plugins_cua.SPECS] == [
        "click",
        "drag",
        "get_app_state",
        "list_apps",
        "press_key",
        "scroll",
        "set_value",
        "type_text",
        "wait",
    ]
    schemas = {spec.name: spec.input_schema for spec in qwen_mm_plugins_cua.SPECS}
    assert "action" not in schemas["click"]["properties"]
    assert "text" not in schemas["click"]["properties"]
    assert "direction" not in schemas["type_text"]["properties"]
    assert all("coordinate_space" not in schema.get("properties", {}) for schema in schemas.values())
    assert schemas["set_value"]["required"] == ["element_token", "value"]
    assert all(schema.get("additionalProperties") is False for schema in schemas.values())


def _profile_registry(profile: str, coordinate_mode: str = "absolute") -> dict:
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QWEN_MM_CUA_TYPE"] = profile
    env["QWEN_MM_CUA_COORDINATE_MODE"] = coordinate_mode
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo / "src"), str(repo / "src" / "capabilities" / "cua"), env.get("PYTHONPATH", "")]
    )
    code = """
import json
import qwen_mm_plugins_cua as cua
print(json.dumps({spec.name: spec.input_schema for spec in cua.SPECS}))
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_native_profile_exposes_only_visual_schemas():
    schemas = _profile_registry("native")

    assert set(schemas) == {
        "click",
        "drag",
        "get_desktop_state",
        "move_cursor",
        "press_key",
        "scroll",
        "type_text",
        "wait",
    }
    assert schemas["click"]["required"] == ["snapshot_id", "x", "y"]
    assert "element_token" not in schemas["click"]["properties"]
    assert schemas["get_desktop_state"]["properties"] == {}
    assert schemas["click"]["properties"]["x"]["minimum"] == 0
    assert "maximum" not in schemas["click"]["properties"]["x"]
    assert schemas["wait"]["properties"]["condition"]["enum"] == ["state_changed", "stable"]
    forbidden = {"app", "pid", "window_id", "element_token", "display_id", "coordinate_space"}
    assert all(not forbidden.intersection(schema.get("properties", {})) for schema in schemas.values())


def test_full_profile_adds_curated_browser_and_runtime_tools():
    schemas = _profile_registry("full")

    assert len(schemas) == 22
    assert {
        "browser_prepare",
        "get_browser_state",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_pointer",
        "browser_dialog",
        "browser_set_input_files",
        "browser_download",
        "get_desktop_state",
        "launch_app",
        "list_windows",
        "verify_state",
    } <= set(schemas)
    assert all(schema.get("additionalProperties") is False for schema in schemas.values())


def test_relative_mode_tightens_only_screenshot_coordinate_schemas():
    native = _profile_registry("native", "relative")
    ax = _profile_registry("ax", "relative")
    full = _profile_registry("full", "relative")

    assert native["click"]["properties"]["x"]["maximum"] == 1000
    assert native["drag"]["properties"]["to_y"]["maximum"] == 1000
    assert ax["click"]["properties"]["x"]["maximum"] == 1000
    assert ax["press_key"]["properties"]["y"]["maximum"] == 1000
    assert "maximum" not in full["browser_click"]["properties"]["x"]


def test_invalid_profile_fails_server_startup():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QWEN_MM_CUA_TYPE"] = "everything"
    env["PYTHONPATH"] = os.pathsep.join([str(repo / "src"), str(repo / "src" / "capabilities" / "cua")])

    result = subprocess.run(
        [sys.executable, "-c", "import qwen_mm_plugins_cua"], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "invalid QWEN_MM_CUA_TYPE" in result.stderr


def test_invalid_coordinate_mode_fails_server_startup():
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QWEN_MM_CUA_COORDINATE_MODE"] = "mixed"
    env["PYTHONPATH"] = os.pathsep.join([str(repo / "src"), str(repo / "src" / "capabilities" / "cua")])

    result = subprocess.run(
        [sys.executable, "-c", "import qwen_mm_plugins_cua"], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "invalid QWEN_MM_CUA_COORDINATE_MODE" in result.stderr


def test_wait_schemas_reject_missing_condition_inputs():
    with pytest.raises(ValueError, match="requires query"):
        wait.WaitArgs.model_validate({"condition": "element_present", "app": "Music"})
    with pytest.raises(ValueError, match="requires snapshot_id"):
        wait.WaitArgs.model_validate({"condition": "state_changed", "app": "Music"})
    with pytest.raises(ValueError, match="requires snapshot_id"):
        native_wait.WaitArgs.model_validate({"condition": "state_changed"})


def test_native_profile_binds_absolute_coordinates_to_primary_desktop(monkeypatch):
    class DesktopClient:
        def __init__(self):
            self.states = [_desktop_state(), _desktop_state("fresh-desktop-image")]
            self.calls = []

        def call(self, tool, arguments, *, timeout=None):
            self.calls.append((tool, arguments, timeout))
            if tool == "get_desktop_state":
                return self.states.pop(0)
            if tool == "click":
                return {"route": "global_input"}
            raise AssertionError(f"unexpected desktop tool: {tool}")

    fake = DesktopClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    observed_blocks = native_desktop_state.handle({})
    observed = _payload(observed_blocks)
    snapshot_id = observed["state"]["snapshot_id"]

    assert observed["target"] == {"kind": "desktop", "display_id": "primary"}
    assert observed["coordinate_space"]["name"] == "pixel"
    assert observed["coordinate_space"]["x_max_exclusive"] == 1600
    assert observed["coordinate_space"]["y_max_exclusive"] == 1200
    assert observed["image_metadata"] == {
        "format": "png",
        "width": 1600,
        "height": 1200,
        "snapshot_binding": snapshot_id,
        "authoritative_for_coordinates": True,
    }
    assert "app" not in observed
    assert observed_blocks[-1]["type"] == "image"

    result = _payload(native_click.handle({"snapshot_id": snapshot_id, "x": 800, "y": 300}))
    click_calls = [args for tool, args, _ in fake.calls if tool == "click"]
    assert click_calls == [
        {
            "target": {"kind": "desktop", "display_id": "primary"},
            "session": driver.DEFAULT_SESSION,
            "x": 800.0,
            "y": 300.0,
            "button": "left",
            "count": 1,
        }
    ]
    assert "pid" not in click_calls[0]
    assert "window_id" not in click_calls[0]
    assert result["interaction"]["driver_results"] == [{"route": "global_input"}]
    assert result["state"]["snapshot_id"] != snapshot_id


def test_native_relative_mode_maps_normalized_coordinates_to_primary_desktop(monkeypatch):
    class DesktopClient:
        def __init__(self):
            self.states = [_desktop_state(), _desktop_state("fresh-desktop-image")]
            self.calls = []

        def call(self, tool, arguments, *, timeout=None):
            self.calls.append((tool, arguments, timeout))
            if tool == "get_desktop_state":
                return self.states.pop(0)
            if tool == "click":
                return {"route": "global_input"}
            raise AssertionError(f"unexpected desktop tool: {tool}")

    monkeypatch.setattr(driver, "CUA_COORDINATE_MODE", "relative")
    fake = DesktopClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    observed_blocks = native_desktop_state.handle({})
    observed = _payload(observed_blocks)
    snapshot_id = observed["state"]["snapshot_id"]

    assert observed["coordinate_space"] == {
        "name": "relative",
        "mode": "relative",
        "x_min": 0,
        "x_max_inclusive": 1000,
        "y_min": 0,
        "y_max_inclusive": 1000,
        "mapped_png_width": 1600,
        "mapped_png_height": 1200,
        "snapshot_binding": snapshot_id,
        "rule": "Coordinates are valid only with this snapshot_id; re-observe after every action.",
    }
    assert "use relative x,y∈[0,1000]" in observed_blocks[-2]["text"]

    _payload(native_click.handle({"snapshot_id": snapshot_id, "x": 500, "y": 250}))
    click_call = next(args for tool, args, _ in fake.calls if tool == "click")
    assert (click_call["x"], click_call["y"]) == pytest.approx((799.5, 299.75))


def test_native_frontmost_keyboard_tools_use_fixed_desktop_target(monkeypatch):
    class DesktopClient:
        def __init__(self):
            self.states = [_desktop_state(), _desktop_state(), _desktop_state()]
            self.calls = []

        def call(self, tool, arguments, *, timeout=None):
            self.calls.append((tool, arguments))
            if tool == "get_desktop_state":
                return self.states.pop(0)
            return {"route": "global_input"}

    fake = DesktopClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    snapshot_id = _payload(native_desktop_state.handle({}))["state"]["snapshot_id"]
    snapshot_id = _payload(native_press_key.handle({"snapshot_id": snapshot_id, "key": "l", "modifiers": ["cmd"]}))[
        "state"
    ]["snapshot_id"]
    _payload(native_type_text.handle({"snapshot_id": snapshot_id, "text": "hello"}))

    actions = [(tool, args) for tool, args in fake.calls if tool != "get_desktop_state"]
    assert actions == [
        (
            "hotkey",
            {
                "target": {"kind": "desktop", "display_id": "primary"},
                "session": driver.DEFAULT_SESSION,
                "keys": ["cmd", "l"],
            },
        ),
        (
            "type_text",
            {
                "target": {"kind": "desktop", "display_id": "primary"},
                "session": driver.DEFAULT_SESSION,
                "text": "hello",
                "delay_ms": 30,
            },
        ),
    ]


def test_native_absolute_coordinate_fails_closed_outside_current_png(monkeypatch):
    class DesktopClient:
        def __init__(self):
            self.calls = []

        def call(self, tool, arguments, *, timeout=None):
            self.calls.append((tool, arguments))
            if tool == "get_desktop_state":
                return _desktop_state()
            raise AssertionError("out-of-range coordinate reached an input tool")

    fake = DesktopClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    snapshot_id = _payload(native_desktop_state.handle({}))["state"]["snapshot_id"]

    blocks = native_click.handle({"snapshot_id": snapshot_id, "x": 1600, "y": 300})

    assert "outside the screenshot range [0, 1600)" in blocks[0]["text"]
    assert [tool for tool, _ in fake.calls] == ["get_desktop_state"]


def test_native_driver_refusal_is_not_reported_as_ok(monkeypatch):
    class RefusingDesktopClient:
        def __init__(self):
            self.states = [_desktop_state(), _desktop_state("fresh-desktop-image")]

        def call(self, tool, arguments, *, timeout=None):
            if tool == "get_desktop_state":
                return self.states.pop(0)
            if tool == "click":
                return {"effect": "refused", "code": "input_not_available"}
            raise AssertionError(tool)

    monkeypatch.setattr(driver, "_CLIENT", RefusingDesktopClient())
    snapshot_id = _payload(native_desktop_state.handle({}))["state"]["snapshot_id"]

    result = _payload(native_click.handle({"snapshot_id": snapshot_id, "x": 10, "y": 10}))

    assert result["ok"] is False
    assert result["interaction"]["driver_accepted"] is False


def test_native_snapshot_is_claimed_once_across_concurrent_actions(monkeypatch):
    class ConcurrentDesktopClient:
        def __init__(self):
            self.clicks = 0
            self.lock = threading.Lock()

        def call(self, tool, arguments, *, timeout=None):
            if tool == "get_desktop_state":
                return _desktop_state()
            if tool == "click":
                with self.lock:
                    self.clicks += 1
                time.sleep(0.03)
                return {"route": "global_input"}
            raise AssertionError(tool)

    fake = ConcurrentDesktopClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    snapshot_id = _payload(native_desktop_state.handle({}))["state"]["snapshot_id"]
    barrier = threading.Barrier(2)
    results = []

    def run():
        barrier.wait(timeout=2)
        results.append(native_click.handle({"snapshot_id": snapshot_id, "x": 10, "y": 10}))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert fake.clicks == 1
    assert sum(result[0]["text"].startswith("Error:") for result in results) == 1


def test_native_snapshot_stays_consumed_when_post_action_observation_fails(monkeypatch):
    class FailingObservationClient:
        def __init__(self):
            self.observations = 0
            self.clicks = 0

        def call(self, tool, arguments, *, timeout=None):
            if tool == "get_desktop_state":
                self.observations += 1
                if self.observations == 1:
                    return _desktop_state()
                raise driver.CuaError("capture failed")
            if tool == "click":
                self.clicks += 1
                return {"route": "global_input"}
            raise AssertionError(tool)

    fake = FailingObservationClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    snapshot_id = _payload(native_desktop_state.handle({}))["state"]["snapshot_id"]

    first = native_click.handle({"snapshot_id": snapshot_id, "x": 10, "y": 10})
    second = native_click.handle({"snapshot_id": snapshot_id, "x": 10, "y": 10})

    assert first[0]["text"] == "Error: capture failed"
    assert "no current desktop snapshot" in second[0]["text"]
    assert fake.clicks == 1


def test_browser_adapter_injects_session_and_emits_image(monkeypatch):
    class BrowserClient:
        def __init__(self):
            self.call_args = None

        def call(self, tool, arguments, *, timeout=None):
            self.call_args = (tool, arguments, timeout)
            return {
                "target_id": "browser-1",
                "tab_id": "tab-1",
                "screenshot_png_b64": "image-data",
                "screenshot_mime_type": "image/png",
            }

    fake = BrowserClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)

    blocks = browser_state_tool.handle({"pid": 123, "window_id": 42})

    assert fake.call_args == (
        "get_browser_state",
        {"pid": 123, "window_id": 42, "session": driver.DEFAULT_SESSION},
        45,
    )
    assert json.loads(blocks[0]["text"])["target_id"] == "browser-1"
    assert blocks[1] == {"type": "image", "data": "image-data", "mimeType": "image/png"}


def test_browser_adapter_marks_driver_refusal_not_ok(monkeypatch):
    class RefusingClient:
        def call(self, tool, arguments, *, timeout=None):
            return {"effect": "refused", "route": "trusted_input"}

    monkeypatch.setattr(driver, "_CLIENT", RefusingClient())

    result = json.loads(browser_state_tool.handle({"pid": 123, "window_id": 42})[0]["text"])

    assert result == {"ok": False, "effect": "refused", "route": "trusted_input"}


def test_driver_refreshes_named_session_before_idle_ttl(monkeypatch):
    calls = []
    now = [0.0]

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", run)
    monkeypatch.setattr(driver.time, "monotonic", lambda: now[0])
    client = driver.DriverClient("/tmp/fake-driver")
    client._version_checked = True

    client.call("get_desktop_state", {"session": "test-run"})
    now[0] = driver.SESSION_REFRESH_SECONDS + 1
    client.call("click", {"session": "test-run", "x": 10, "y": 20})

    assert [argv[2] for argv in calls] == ["start_session", "get_desktop_state", "start_session", "click"]


def test_default_session_is_unique_to_this_server_process():
    assert driver.DEFAULT_SESSION.startswith(f"qwen-mm-cua-{os.getpid()}-")
    assert len(driver.DEFAULT_SESSION) < 80


def test_ending_an_unknown_session_does_not_revive_it_first(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", run)
    client = driver.DriverClient("/tmp/fake-driver")
    client._version_checked = True

    client.call("end_session", {"session": "already-ended"})

    assert [argv[2] for argv in calls] == ["end_session"]


def test_driver_requires_020_or_newer(tmp_path):
    binary = tmp_path / "cua-driver"
    binary.write_text("#!/bin/sh\necho 'cua-driver 0.19.3'\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(driver.CuaError, match=r"0\.20\.0\+"):
        driver.DriverClient(str(binary)).ensure_compatible()


def test_main_window_selection_rejects_narrow_surface():
    selected, reason = driver.select_window([NARROW_WINDOW, MAIN_WINDOW])

    assert selected["window_id"] == 42
    assert reason["rejected_window_ids"] == [7]


def test_absolute_coordinates_bind_to_png_dimensions(monkeypatch):
    _install_fake(monkeypatch, _state(1))
    state = _payload(get_app_state.handle({"app": "Music"}))
    assert state["pixel_actions"]["snapshot_binding"] == "s00000001"
    assert state["pixel_actions"]["coordinate_space"] == "pixel"

    record = driver.SNAPSHOTS._by_id["s00000001"]
    assert driver.convert_point(record, 500, 250) == (500.0, 250.0)


def test_ax_relative_mode_is_advertised_and_maps_to_png_dimensions(monkeypatch):
    monkeypatch.setattr(driver, "CUA_COORDINATE_MODE", "relative")
    fake = _install_fake(monkeypatch, _state(1), _state(2, label="Opened", screenshot="changed"))
    observed = _payload(get_app_state.handle({"app": "Music"}))

    assert observed["pixel_actions"]["coordinate_space"] == "relative"
    assert observed["pixel_actions"]["coordinate_mode"] == "relative"
    assert observed["coordinate_space"]["x_max_inclusive"] == 1000
    assert observed["coordinate_space"]["mapped_png_width"] == 1600

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "x": 500,
                "y": 250,
            }
        )
    )
    click_call = next(args for tool, args in fake.calls if tool == "click")
    assert (click_call["x"], click_call["y"]) == pytest.approx((799.5, 299.75))
    assert result["interaction"]["attempts"][0]["address"] == "relative"


def test_relative_mode_rejects_coordinates_outside_zero_to_one_thousand(monkeypatch):
    monkeypatch.setattr(driver, "CUA_COORDINATE_MODE", "relative")
    fake = _install_fake(monkeypatch, _state(1))
    _payload(get_app_state.handle({"app": "Music"}))

    blocks = click.handle(
        {
            "app": "Music",
            "snapshot_id": "s00000001",
            "x": 1001,
            "y": 500,
        }
    )

    assert "between 0 and 1000 in relative coordinate mode" in blocks[0]["text"]
    assert not [tool for tool, _ in fake.calls if tool == "click"]


def test_image_is_immediately_preceded_by_its_absolute_coordinate_frame(monkeypatch):
    _install_fake(monkeypatch, _state(1))

    blocks = get_app_state.handle({"app": "Music"})
    payload = _payload(blocks)

    assert payload["image_metadata"] == {
        "format": "png",
        "width": 1600,
        "height": 1200,
        "snapshot_binding": "s00000001",
        "authoritative_for_coordinates": True,
    }
    assert blocks[-2]["type"] == "text"
    assert "Authoritative encoded-PNG metadata" in blocks[-2]["text"]
    assert "H×W=1200×1600 px" in blocks[-2]["text"]
    assert "x∈[0,1600), y∈[0,1200)" in blocks[-2]["text"]
    assert blocks[-1]["type"] == "image"


def test_click_reobserves_and_verifies_state_change(monkeypatch):
    fake = _install_fake(monkeypatch, _state(1), _state(2, label="Song detail", screenshot="changed"))
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "expect": {"condition": "state_changed"},
            }
        )
    )

    assert result["state"]["snapshot_id"] == "s00000002"
    assert result["interaction"]["verification"]["verified"] is True
    click_calls = [args for tool, args in fake.calls if tool == "click"]
    assert click_calls == [
        {
            "pid": 123,
            "window_id": 42,
            "session": driver.DEFAULT_SESSION,
            "delivery_mode": "background",
            "element_token": "s00000001:1",
            "button": "left",
        }
    ]


def test_ax_driver_refusal_is_not_reported_as_ok(monkeypatch):
    class RefusingClient(FakeClient):
        def call(self, tool: str, arguments: dict | None = None, *, timeout=None) -> dict:
            if tool == "click":
                self.calls.append((tool, dict(arguments or {})))
                return {"effect": "refused", "code": "input_not_available"}
            return super().call(tool, arguments, timeout=timeout)

    fake = RefusingClient([_state(1), _state(2)])
    monkeypatch.setattr(driver, "_CLIENT", fake)
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
            }
        )
    )

    assert result["ok"] is False
    assert result["interaction"]["driver_accepted"] is False
    assert result["interaction"]["attempts"][0]["accepted"] is False


def test_ax_action_reuses_the_session_bound_to_its_snapshot(monkeypatch):
    fake = _install_fake(monkeypatch, _state(1), _state(2))
    _payload(get_app_state.handle({"app": "Music", "session": "custom-run"}))

    _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
            }
        )
    )

    state_calls = [args for tool, args in fake.calls if tool == "get_window_state"]
    click_call = next(args for tool, args in fake.calls if tool == "click")
    assert [args["session"] for args in state_calls] == ["custom-run", "custom-run"]
    assert click_call["session"] == "custom-run"


def test_ax_snapshot_is_claimed_once_across_concurrent_actions(monkeypatch):
    class ConcurrentAxClient(FakeClient):
        def __init__(self):
            super().__init__([_state(1), _state(2)])
            self.clicks = 0
            self.lock = threading.Lock()

        def call(self, tool: str, arguments: dict | None = None, *, timeout=None) -> dict:
            if tool == "click":
                with self.lock:
                    self.clicks += 1
                time.sleep(0.03)
            return super().call(tool, arguments, timeout=timeout)

    fake = ConcurrentAxClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    _payload(get_app_state.handle({"app": "Music"}))
    barrier = threading.Barrier(2)
    results = []

    def run():
        barrier.wait(timeout=2)
        results.append(
            click.handle(
                {
                    "app": "Music",
                    "snapshot_id": "s00000001",
                    "element_token": "s00000001:1",
                }
            )
        )

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert fake.clicks == 1
    assert sum(result[0]["text"].startswith("Error:") for result in results) == 1


def test_failed_explicit_expectation_is_not_reported_as_ok(monkeypatch):
    _install_fake(monkeypatch, _state(1), _state(2))
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "expect": {"condition": "element_present", "query": "Never appears"},
            }
        )
    )

    assert result["interaction"]["driver_accepted"] is True
    assert result["interaction"]["verification"]["verified"] is False
    assert result["ok"] is False


def test_safe_auto_retry_converts_element_center_to_pixel(monkeypatch):
    fake = _install_fake(
        monkeypatch,
        _state(1),
        _state(2, label="Unrelated change", screenshot="cursor-moved"),
        _state(3, label="Opened", screenshot="changed"),
    )
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "retry_if_unverified": True,
                "expect": {"condition": "element_present", "query": "Opened"},
            }
        )
    )

    click_calls = [args for tool, args in fake.calls if tool == "click"]
    assert len(click_calls) == 2
    assert click_calls[0]["element_token"] == "s00000001:1"
    assert click_calls[1]["delivery_mode"] == "background"
    assert (click_calls[1]["x"], click_calls[1]["y"]) == pytest.approx((60, 60))
    assert result["interaction"]["verification"]["verified"] is True


def test_explicit_global_pointer_fallback_maps_and_verifies_desktop_click(monkeypatch):
    fake = _install_fake(
        monkeypatch,
        _state(1),
        _state(2),
        _state(3),
        _state(4),
        _state(5, label="Opened", screenshot="changed"),
    )
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "retry_if_unverified": True,
                "expect": {"condition": "element_present", "query": "Opened"},
            }
        )
    )

    click_calls = [args for tool, args in fake.calls if tool == "click"]
    assert len(click_calls) == 4
    assert [call.get("delivery_mode") for call in click_calls[:3]] == ["background", "background", "foreground"]
    assert click_calls[3] == {
        "scope": "desktop",
        "x": 260,
        "y": 260,
        "button": "left",
        "session": driver.DEFAULT_SESSION,
    }
    fallback = result["interaction"]["global_pointer_fallback"]
    assert fallback["attempted"] is True
    assert fallback["route_verified"] is True
    assert fallback["cursor_readback"] == {"x": 130.0, "y": 130.0}
    assert result["interaction"]["verification"]["verified"] is True


def test_safe_click_retry_continues_after_ax_driver_error(monkeypatch):
    fake = AxClickFailureClient([_state(1), _state(2), _state(3, label="Opened", screenshot="changed")])
    monkeypatch.setattr(driver, "_CLIENT", fake)
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "retry_if_unverified": True,
                "expect": {"condition": "element_present", "query": "Opened"},
            }
        )
    )

    attempts = result["interaction"]["attempts"]
    assert attempts[0]["driver_error"] == "AX action failed"
    assert attempts[1]["address"] == "pixel_fallback"
    assert result["interaction"]["verification"]["verified"] is True


def test_coordinate_retry_does_not_repeat_the_same_background_pixel(monkeypatch):
    fake = _install_fake(
        monkeypatch,
        _state(1),
        _state(2),
        _state(3, label="Opened", screenshot="changed"),
    )
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "x": 60,
                "y": 60,
                "retry_if_unverified": True,
                "expect": {"condition": "element_present", "query": "Opened"},
            }
        )
    )

    calls = [args for tool, args in fake.calls if tool == "click"]
    assert [call["delivery_mode"] for call in calls] == ["background", "foreground"]
    assert result["interaction"]["verification"]["verified"] is True


def test_global_pointer_fallback_refuses_an_off_space_window(monkeypatch):
    monkeypatch.setattr("qwen_mm_plugins_cua.tools._actions.sys.platform", "darwin")
    fake = _install_fake(monkeypatch, _state(1), _state(2), _state(3), _state(4))
    off_space = {**MAIN_WINDOW, "on_current_space": False}

    original_call = fake.call

    def call_with_off_space(tool: str, arguments: dict | None = None, *, timeout=None):
        if tool == "list_windows":
            fake.calls.append((tool, dict(arguments or {})))
            return {"windows": [NARROW_WINDOW, off_space]}
        return original_call(tool, arguments, timeout=timeout)

    monkeypatch.setattr(fake, "call", call_with_off_space)
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "retry_if_unverified": True,
                "allow_global_pointer_fallback": True,
                "expect": {"condition": "element_present", "query": "Opened"},
            }
        )
    )

    fallback = result["interaction"]["global_pointer_fallback"]
    assert fallback["attempted"] is False
    assert "current Space" in fallback["refused"]
    assert not [args for tool, args in fake.calls if tool == "click" and args.get("scope") == "desktop"]


def test_global_pointer_fallback_schema_requires_safe_retry_contract():
    ordinary = click.ClickArgs.model_validate(
        {
            "app": "Music",
            "snapshot_id": "s00000001",
            "x": 10,
            "y": 10,
        }
    )
    assert ordinary.allow_global_pointer_fallback is True

    with pytest.raises(ValueError, match="global pointer fallback requires"):
        click.ClickArgs.model_validate(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "x": 10,
                "y": 10,
                "delivery": "foreground",
                "retry_if_unverified": True,
            }
        )


def test_stale_snapshot_fails_before_input(monkeypatch):
    fake = _install_fake(monkeypatch, _state(1), _state(2))
    _payload(get_app_state.handle({"app": "Music"}))
    _payload(get_app_state.handle({"app": "Music"}))

    blocks = click.handle(
        {
            "app": "Music",
            "snapshot_id": "s00000001",
            "element_token": "s00000001:1",
        }
    )

    assert "stale snapshot_id" in blocks[0]["text"]
    assert not [tool for tool, _ in fake.calls if tool == "click"]


def test_wait_returns_new_snapshot_when_condition_appears(monkeypatch):
    _install_fake(monkeypatch, _state(1), _state(2), _state(3, label="Ready"))
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        wait.handle(
            {
                "condition": "element_present",
                "app": "Music",
                "query": "Ready",
                "timeout_seconds": 1,
                "poll_interval_seconds": 0.01,
                "include_screenshot": False,
            }
        )
    )

    assert result["wait_result"]["satisfied"] is True
    assert result["state"]["snapshot_id"] == "s00000003"
    assert result["ok"] is True


def test_wait_verifies_the_same_state_it_returns_with_a_screenshot(monkeypatch):
    class ScreenshotSensitiveClient(FakeClient):
        def __init__(self):
            super().__init__([_state(1)])
            self.snapshot = 1

        def call(self, tool: str, arguments: dict | None = None, *, timeout=None) -> dict:
            if tool == "get_window_state" and not self.states:
                args = dict(arguments or {})
                self.calls.append((tool, args))
                self.snapshot += 1
                if args.get("include_screenshot"):
                    return _state(self.snapshot, label="Gone", screenshot="fresh-image")
                state = _state(self.snapshot, label="Ready")
                state.pop("screenshot_png_b64", None)
                return state
            return super().call(tool, arguments, timeout=timeout)

    fake = ScreenshotSensitiveClient()
    monkeypatch.setattr(driver, "_CLIENT", fake)
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        wait.handle(
            {
                "condition": "element_present",
                "app": "Music",
                "query": "Ready",
                "timeout_seconds": 0.03,
                "poll_interval_seconds": 0.01,
            }
        )
    )

    wait_observations = [args for tool, args in fake.calls if tool == "get_window_state"][1:]
    assert all(args["include_screenshot"] is True for args in wait_observations)
    assert result["ok"] is False
    assert result["wait_result"]["satisfied"] is False
    assert result["state"]["tree_markdown"] == "Music\n  AXCell Gone"


def test_wait_does_not_expose_coordinates_when_internal_comparison_image_is_hidden(monkeypatch):
    _install_fake(monkeypatch, _state(1), _state(2, label="Ready", screenshot="fresh-image"))
    _payload(get_app_state.handle({"app": "Music"}))

    blocks = wait.handle(
        {
            "condition": "element_present",
            "app": "Music",
            "query": "Ready",
            "use_screenshot": True,
            "include_screenshot": False,
            "timeout_seconds": 0.03,
            "poll_interval_seconds": 0.01,
        }
    )
    result = _payload(blocks)

    assert len(blocks) == 1
    assert result["pixel_actions"]["available"] is False
    coordinate_attempt = click.handle(
        {
            "app": "Music",
            "snapshot_id": result["state"]["snapshot_id"],
            "x": 10,
            "y": 10,
        }
    )
    assert "screenshot coordinates are unavailable" in coordinate_attempt[0]["text"]


def test_observation_retries_without_repeating_input(monkeypatch):
    fake = TransientStateClient([_state(1), {"status": "refused"}, _state(2, label="Opened")])
    monkeypatch.setattr(driver, "_CLIENT", fake)
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        click.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
            }
        )
    )

    assert result["state"]["snapshot_id"] == "s00000002"
    assert len([tool for tool, _ in fake.calls if tool == "click"]) == 1
    assert len([tool for tool, _ in fake.calls if tool == "get_window_state"]) == 3


def test_list_apps_filters_without_resolving_a_window(monkeypatch):
    fake = _install_fake(monkeypatch)

    result = _payload(list_apps.handle({"query": "apple.music", "running_only": True}))

    assert result["count"] == 1
    assert result["apps"][0]["bundle_id"] == "com.apple.Music"
    assert [tool for tool, _ in fake.calls] == ["list_apps"]


def test_type_text_has_a_narrow_payload_and_returns_fresh_state(monkeypatch):
    fake = _install_fake(monkeypatch, _state(1), _state(2, label="Typed", screenshot="changed"))
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        type_text.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "text": "爱错",
            }
        )
    )

    calls = [args for tool, args in fake.calls if tool == "type_text"]
    assert calls == [
        {
            "pid": 123,
            "window_id": 42,
            "session": driver.DEFAULT_SESSION,
            "delivery_mode": "background",
            "element_token": "s00000001:1",
            "text": "爱错",
            "delay_ms": 30,
        }
    ]
    assert result["state"]["snapshot_id"] == "s00000002"


def test_press_key_combines_hotkey_modifiers_and_repeat(monkeypatch):
    fake = _install_fake(monkeypatch, _state(1), _state(2, label="Selected", screenshot="changed"))
    _payload(get_app_state.handle({"app": "Music"}))

    _payload(
        press_key.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "key": "down",
                "modifiers": ["shift"],
                "repeat": 2,
            }
        )
    )

    calls = [args for tool, args in fake.calls if tool == "hotkey"]
    assert len(calls) == 2
    assert all(call["keys"] == ["shift", "down"] for call in calls)
    assert not [args for tool, args in fake.calls if tool == "press_key"]


def test_set_value_uses_only_the_snapshot_bound_element(monkeypatch):
    fake = _install_fake(monkeypatch, _state(1), _state(2, label="Volume", screenshot="changed"))
    _payload(get_app_state.handle({"app": "Music"}))

    result = _payload(
        set_value.handle(
            {
                "app": "Music",
                "snapshot_id": "s00000001",
                "element_token": "s00000001:1",
                "value": "50",
            }
        )
    )

    calls = [args for tool, args in fake.calls if tool == "set_value"]
    assert calls == [
        {
            "pid": 123,
            "window_id": 42,
            "session": driver.DEFAULT_SESSION,
            "element_token": "s00000001:1",
            "value": "50",
        }
    ]
    assert result["state"]["snapshot_id"] == "s00000002"
