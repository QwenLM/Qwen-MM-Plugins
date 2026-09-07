"""Small, fail-closed adapter around the installed ``cua-driver`` CLI.

The upstream driver owns OS permissions, capture, coordinate transforms, and input delivery. This
module deliberately does not proxy its full MCP surface: it resolves an app/window, keeps the last
snapshot for that exact target, and supports the small action-specific surface shipped by this plugin.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from qwen_mm_plugins_cua.mode import CUA_COORDINATE_MODE
from shared.content import image, text
from shared.env import get_env

MIN_DRIVER_VERSION = (0, 20, 0)
SESSION_REFRESH_SECONDS = 240.0
DEFAULT_SESSION = f"qwen-mm-cua-{os.getpid()}-{secrets.token_hex(4)}"
SCREENSHOT_KEY = "screenshot_png_b64"
_VERSION_RE = re.compile(r"(?:cua-driver(?:-rs)?\s+)?(\d+)\.(\d+)\.(\d+)")
_SNAPSHOT_RE = re.compile(r"^(s[0-9a-f]{8})")


class CuaError(RuntimeError):
    """A user-facing, fail-closed adapter error."""


def driver_result_refused(result: dict[str, Any]) -> bool:
    """Recognize every documented Driver refusal marker without guessing from prose."""
    status = result.get("status")
    return (
        result.get("ok") is False
        or bool(result.get("refusal"))
        or status in {"error", "failed", "refused"}
        or result.get("effect") == "refused"
        or bool(result.get("code"))
    )


def resolve_driver_binary() -> str:
    """Resolve the Cua Driver binary without assuming GUI-launched apps inherit shell PATH."""
    configured = get_env("QWEN_MM_CUA_DRIVER_PATH")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path)
        raise CuaError(f"QWEN_MM_CUA_DRIVER_PATH is not executable: {path}")

    for binary_name in ("cua-driver", "cua-driver-local"):
        if found := shutil.which(binary_name):
            return found
    candidates = [
        Path.home() / ".local" / "bin" / "cua-driver",
        Path.home() / ".local" / "bin" / "cua-driver-local",
    ]
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/CuaDriver.app/Contents/MacOS/cua-driver"),
                Path("/Applications/CuaDriverLocal.app/Contents/MacOS/cua-driver-local"),
            ]
        )
    for path in candidates:
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path)
    raise CuaError(
        "Cua Driver was not found. Install the matching Driver build described in the CUA "
        "cookbook or set QWEN_MM_CUA_DRIVER_PATH."
    )


class DriverClient:
    """One-shot CLI client. The driver daemon remains persistent; only the proxy call is short-lived."""

    def __init__(self, binary: str | None = None, *, timeout: float = 30.0) -> None:
        self.binary = binary or resolve_driver_binary()
        self.timeout = timeout
        self._version_checked = False
        self._session_last_used: dict[str, float] = {}
        self._lock = threading.RLock()

    def ensure_compatible(self) -> tuple[int, int, int]:
        with self._lock:
            proc = subprocess.run(
                [self.binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = (proc.stdout or proc.stderr).strip()
            match = _VERSION_RE.search(output)
            if proc.returncode != 0 or not match:
                raise CuaError(f"could not determine Cua Driver version from {self.binary}: {output}")
            version = tuple(int(part) for part in match.groups())
            if version < MIN_DRIVER_VERSION:
                minimum = ".".join(map(str, MIN_DRIVER_VERSION))
                raise CuaError(f"Cua Driver {minimum}+ is required; found {'.'.join(map(str, version))}")
            self._version_checked = True
            return version

    def call(self, tool: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict:
        with self._lock:
            if not self._version_checked:
                self.ensure_compatible()
            arguments = arguments or {}
            session = arguments.get("session")
            if tool not in {"start_session", "end_session"} and isinstance(session, str):
                last_used = self._session_last_used.get(session)
                if last_used is None or time.monotonic() - last_used >= SESSION_REFRESH_SECONDS:
                    started = self.call("start_session", {"session": session})
                    if driver_result_refused(started):
                        raise CuaError(f"cua-driver start_session refused: {started}")
            encoded = json.dumps(arguments or {}, ensure_ascii=False, separators=(",", ":"))
            try:
                proc = subprocess.run(
                    [self.binary, "call", tool, encoded],
                    capture_output=True,
                    text=True,
                    timeout=timeout or self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CuaError(f"cua-driver {tool} timed out after {exc.timeout:g}s") from exc
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).strip()
                raise CuaError(f"cua-driver {tool} failed: {detail or f'exit {proc.returncode}'}")
            try:
                result = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                preview = proc.stdout.strip()[:300]
                raise CuaError(f"cua-driver {tool} returned invalid JSON: {preview}") from exc
            if not isinstance(result, dict):
                raise CuaError(f"cua-driver {tool} returned {type(result).__name__}, expected an object")
            if isinstance(session, str) and not driver_result_refused(result):
                if tool == "end_session":
                    self._session_last_used.pop(session, None)
                else:
                    self._session_last_used[session] = time.monotonic()
            return result


_CLIENT: DriverClient | Any | None = None
_CLIENT_LOCK = threading.Lock()


def get_client() -> DriverClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = DriverClient()
        return _CLIENT


@dataclass(frozen=True)
class Target:
    pid: int
    window_id: int
    app: dict[str, Any]
    window: dict[str, Any]
    selection: dict[str, Any]


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: str
    target: Target
    state: dict[str, Any]
    width: int | None
    height: int | None
    frame_valid: bool
    ax_hash: str
    visual_hash: str | None
    projected: bool
    session: str


class SnapshotStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: dict[tuple[int, int], SnapshotRecord] = {}
        self._by_id: dict[str, SnapshotRecord] = {}
        self._target_locks: dict[tuple[int, int], threading.RLock] = {}

    @staticmethod
    def _key(target: Target) -> tuple[int, int]:
        return target.pid, target.window_id

    @contextmanager
    def transaction(self, target: Target) -> Iterator[None]:
        key = self._key(target)
        with self._lock:
            target_lock = self._target_locks.setdefault(key, threading.RLock())
        with target_lock:
            yield

    def put(self, record: SnapshotRecord) -> None:
        key = self._key(record.target)
        with self._lock:
            old = self._current.get(key)
            if old is not None:
                self._by_id.pop(old.snapshot_id, None)
            self._current[key] = record
            self._by_id[record.snapshot_id] = record

    def current(self, target: Target) -> SnapshotRecord | None:
        with self._lock:
            return self._current.get(self._key(target))

    def consume(self, target: Target, snapshot_id: str | None) -> SnapshotRecord:
        """Atomically validate and invalidate one snapshot before any input can be delivered."""
        with self._lock:
            current = self._current.get(self._key(target))
            if current is None:
                raise CuaError("no current snapshot for this window; call get_app_state first")
            if not snapshot_id:
                raise CuaError("snapshot_id is required; copy it from the latest get_app_state result")
            if snapshot_id != current.snapshot_id:
                raise CuaError(
                    f"stale snapshot_id {snapshot_id!r}; the current snapshot for this window is "
                    f"{current.snapshot_id!r}. Re-observe before acting."
                )
            self._current.pop(self._key(target), None)
            self._by_id.pop(current.snapshot_id, None)
            return current

    def clear(self) -> None:
        with self._lock:
            self._current.clear()
            self._by_id.clear()
            self._target_locks.clear()


SNAPSHOTS = SnapshotStore()


def _number(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _window_geometry(window: dict[str, Any]) -> tuple[float, float, float]:
    bounds = window.get("bounds") if isinstance(window.get("bounds"), dict) else {}
    width = max(0.0, _number(bounds.get("width")))
    height = max(0.0, _number(bounds.get("height")))
    area = width * height
    return width, height, area


def is_pathological_surface(window: dict[str, Any]) -> bool:
    """Reject menu strips, helper surfaces, and captures whose coordinate map is not useful."""
    width, height, area = _window_geometry(window)
    if width < 80 or height < 80 or area < 10_000:
        return True
    aspect = max(width / height, height / width)
    return not math.isfinite(aspect) or aspect > 16


def select_window(windows: list[dict[str, Any]], window_id: int | None = None) -> tuple[dict, dict]:
    """Select an ordinary main surface and explain which pathological candidates were rejected."""
    if window_id is not None:
        for window in windows:
            if window.get("window_id") == window_id:
                if is_pathological_surface(window):
                    raise CuaError(f"window_id {window_id} is a pathological/tiny surface; choose a real app window")
                return window, {"strategy": "explicit_window_id", "rejected_window_ids": []}
        raise CuaError(f"window_id {window_id} was not found for the target process")

    eligible = [window for window in windows if not is_pathological_surface(window) and window.get("layer", 0) == 0]
    rejected = [window.get("window_id") for window in windows if window not in eligible]
    if not eligible:
        raise CuaError("no ordinary app window is available; only tiny, narrow, or non-layer-0 surfaces were found")

    def score(window: dict[str, Any]) -> tuple[int, int, int, float]:
        _, _, area = _window_geometry(window)
        return (
            int(window.get("on_current_space") is True),
            int(window.get("is_on_screen") is True),
            int(bool(str(window.get("title") or "").strip())),
            area,
        )

    chosen = max(eligible, key=score)
    return chosen, {
        "strategy": "ordinary_layer0_current_space_then_area",
        "candidate_count": len(windows),
        "eligible_count": len(eligible),
        "rejected_window_ids": [value for value in rejected if value is not None],
    }


def _app_matches(app: dict[str, Any], query: str) -> tuple[int, int]:
    needle = query.strip().casefold()
    fields = [str(app.get(key) or "").casefold() for key in ("name", "bundle_id", "launch_path")]
    exact = int(any(value == needle for value in fields))
    partial = int(any(needle in value for value in fields if value))
    return exact, partial


def resolve_app(client: DriverClient, *, app: str | None, pid: int | None, launch_if_needed: bool) -> dict:
    apps = client.call("list_apps").get("apps", [])
    if not isinstance(apps, list):
        raise CuaError("cua-driver list_apps returned no apps array")
    if pid is not None:
        matches = [item for item in apps if item.get("pid") == pid and item.get("running")]
        if not matches:
            raise CuaError(f"no running regular application has pid {pid}")
        if app and not _app_matches(matches[0], app)[1]:
            raise CuaError(f"pid {pid} does not match app {app!r}")
        return matches[0]
    if not app or not app.strip():
        raise CuaError("provide app (name or bundle id) or pid")

    ranked = [(item, _app_matches(item, app)) for item in apps]
    candidates = [(item, rank) for item, rank in ranked if rank[1]]
    if not candidates:
        raise CuaError(f"application {app!r} was not found")
    best_rank = max(rank for _, rank in candidates)
    best = [item for item, rank in candidates if rank == best_rank]
    running = [item for item in best if item.get("running")]
    if len(running) == 1:
        return running[0]
    if len(running) > 1:
        raise CuaError(f"application {app!r} is ambiguous across running apps; pass pid or bundle id")
    if len(best) > 1:
        bundle_ids = sorted({str(item.get("bundle_id")) for item in best})
        raise CuaError(f"application {app!r} is ambiguous: {', '.join(bundle_ids)}")
    if not launch_if_needed:
        raise CuaError(f"application {app!r} is installed but not running; launch it first")

    candidate = best[0]
    launch_args = (
        {"bundle_id": candidate.get("bundle_id")} if candidate.get("bundle_id") else {"name": candidate.get("name")}
    )
    launched = client.call("launch_app", launch_args, timeout=45)
    launched_pid = launched.get("pid")
    if not isinstance(launched_pid, int) or launched_pid <= 0:
        raise CuaError(f"launch_app did not return a live pid for {app!r}")
    return {**candidate, **launched, "pid": launched_pid, "running": True}


def resolve_target(
    client: DriverClient,
    *,
    app: str | None,
    pid: int | None,
    window_id: int | None,
    launch_if_needed: bool = False,
    window_timeout: float = 6.0,
) -> Target:
    app_info = resolve_app(client, app=app, pid=pid, launch_if_needed=launch_if_needed)
    resolved_pid = int(app_info["pid"])
    deadline = time.monotonic() + window_timeout
    windows: list[dict[str, Any]] = []
    while True:
        response = client.call("list_windows", {"pid": resolved_pid})
        raw = response.get("windows", [])
        windows = raw if isinstance(raw, list) else []
        try:
            window, selection = select_window(windows, window_id)
            return Target(resolved_pid, int(window["window_id"]), app_info, window, selection)
        except CuaError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _clean_for_ax_hash(state: dict[str, Any]) -> dict[str, Any]:
    ignored = {
        SCREENSHOT_KEY,
        "screenshot_file_path",
        "screenshot_mime_type",
        "snapshot_id",
        "_note",
        "background_input",
        "tree_markdown",
    }
    cleaned = {key: value for key, value in state.items() if key not in ignored}
    elements = []
    for element in cleaned.get("elements", []):
        if isinstance(element, dict):
            elements.append({key: value for key, value in element.items() if key != "element_token"})
    if "elements" in cleaned:
        cleaned["elements"] = elements
    return cleaned


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def record_snapshot(target: Target, state: dict[str, Any], *, session: str = DEFAULT_SESSION) -> SnapshotRecord:
    snapshot_id = state.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_RE.fullmatch(snapshot_id):
        raise CuaError("get_window_state returned no valid snapshot_id")
    screenshot = state.get(SCREENSHOT_KEY)
    width = state.get("screenshot_width")
    height = state.get("screenshot_height")
    valid_dimensions = isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0
    frame_valid = bool(state.get("screenshot_frame_valid")) and valid_dimensions and isinstance(screenshot, str)
    reduced_state = {key: value for key, value in state.items() if key != SCREENSHOT_KEY}
    record = SnapshotRecord(
        snapshot_id=snapshot_id,
        target=target,
        state=reduced_state,
        width=width if valid_dimensions else None,
        height=height if valid_dimensions else None,
        frame_valid=frame_valid,
        ax_hash=_hash_json(_clean_for_ax_hash(state)),
        visual_hash=_perceptual_hash(screenshot) if isinstance(screenshot, str) else None,
        projected="filtered_element_count" in state,
        session=session,
    )
    SNAPSHOTS.put(record)
    return record


def observe_target(
    client: DriverClient,
    target: Target,
    *,
    query: str | None = None,
    include_screenshot: bool = True,
    max_elements: int = 600,
    max_depth: int = 20,
    session: str = DEFAULT_SESSION,
) -> tuple[dict[str, Any], SnapshotRecord]:
    arguments: dict[str, Any] = {
        "pid": target.pid,
        "window_id": target.window_id,
        "include_screenshot": include_screenshot,
        "max_elements": max_elements,
        "max_depth": max_depth,
        "session": session,
    }
    if query:
        arguments["query"] = query
    state: dict[str, Any] = {}
    with SNAPSHOTS.transaction(target):
        for attempt in range(3):
            state = client.call("get_window_state", arguments, timeout=45)
            snapshot_id = state.get("snapshot_id")
            if isinstance(snapshot_id, str) and _SNAPSHOT_RE.fullmatch(snapshot_id):
                return state, record_snapshot(target, state, session=session)
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    detail = state.get("refusal") or state.get("error") or state.get("status") or state
    raise CuaError(f"get_window_state returned no valid snapshot_id after 3 observations: {detail}")


def get_state(
    *,
    app: str | None,
    pid: int | None,
    window_id: int | None,
    launch_if_needed: bool,
    query: str | None,
    include_screenshot: bool,
    max_elements: int,
    max_depth: int,
    session: str,
) -> tuple[Target, dict[str, Any], SnapshotRecord]:
    client = get_client()
    target = resolve_target(
        client,
        app=app,
        pid=pid,
        window_id=window_id,
        launch_if_needed=launch_if_needed,
    )
    state, record = observe_target(
        client,
        target,
        query=query,
        include_screenshot=include_screenshot,
        max_elements=max_elements,
        max_depth=max_depth,
        session=session,
    )
    return target, state, record


def state_content(
    target: Target,
    state: dict[str, Any],
    record: SnapshotRecord,
    *,
    extra: dict[str, Any] | None = None,
    ok: bool = True,
) -> list[dict[str, str]]:
    screenshot = state.get(SCREENSHOT_KEY)
    public_state = {key: value for key, value in state.items() if key != SCREENSHOT_KEY}
    coordinate_space = (
        coordinate_contract(record.width, record.height, record.snapshot_id)
        if record.frame_valid and record.width is not None and record.height is not None
        else None
    )
    payload: dict[str, Any] = {
        "ok": ok,
        "app": {key: target.app.get(key) for key in ("name", "bundle_id", "pid", "active", "running")},
        "selected_window": target.window,
        "window_selection": target.selection,
        "pixel_actions": {
            "available": record.frame_valid,
            "coordinate_space": coordinate_space["name"] if coordinate_space else None,
            "coordinate_mode": CUA_COORDINATE_MODE,
            "snapshot_binding": record.snapshot_id,
            "rule": "Coordinates are valid only with this snapshot_id; re-observe after every action.",
        },
        "state": public_state,
    }
    if coordinate_space:
        payload["coordinate_space"] = coordinate_space
    if isinstance(screenshot, str) and record.width is not None and record.height is not None:
        payload["image_metadata"] = {
            "format": "png",
            "width": record.width,
            "height": record.height,
            "snapshot_binding": record.snapshot_id,
            "authoritative_for_coordinates": record.frame_valid,
        }
    if extra:
        payload.update(extra)
    blocks = [text(json.dumps(payload, ensure_ascii=False, indent=2))]
    if isinstance(screenshot, str):
        if record.frame_valid and record.width is not None and record.height is not None:
            blocks.append(text(coordinate_instruction(record.width, record.height, record.snapshot_id)))
        else:
            blocks.append(text("Screenshot coordinate frame is unproven; do not use screenshot coordinates."))
        blocks.append(image(screenshot, state.get("screenshot_mime_type", "image/png")))
    return blocks


def _perceptual_hash(screenshot: str) -> str | None:
    """Return a 1024-bit dHash; small cursor-overlay changes should not count as a new UI state."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(base64.b64decode(screenshot))) as source:
            pixels = list(source.convert("L").resize((33, 32)).getdata())
    except Exception:
        return None
    bits = 0
    for row in range(32):
        offset = row * 33
        for column in range(32):
            bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return f"{bits:0256x}"


def compare_snapshots(before: SnapshotRecord, after: SnapshotRecord) -> dict[str, bool | float | None]:
    visual_changed = None
    visual_change_score = None
    if before.visual_hash is not None and after.visual_hash is not None:
        distance = (int(before.visual_hash, 16) ^ int(after.visual_hash, 16)).bit_count()
        visual_change_score = round(distance / 1024, 4)
        visual_changed = distance > 20
    ax_changed = None if before.projected or after.projected else before.ax_hash != after.ax_hash
    return {
        "ax_changed": ax_changed,
        "visual_changed": visual_changed,
        "visual_change_score": visual_change_score,
        "state_changed": ax_changed is True or visual_changed is True,
    }


def _state_haystack(state: dict[str, Any]) -> str:
    parts = [str(state.get("tree_markdown") or "")]
    for element in state.get("elements", []):
        if isinstance(element, dict):
            parts.extend(str(value) for value in element.values() if isinstance(value, (str, int, float, bool)))
    return "\n".join(parts).casefold()


def evaluate_condition(
    condition: str,
    state: dict[str, Any],
    *,
    query: str | None = None,
    value: str | None = None,
    before: SnapshotRecord | None = None,
    after: SnapshotRecord | None = None,
) -> tuple[bool, str]:
    if condition == "state_changed":
        if before is None or after is None:
            return False, "state_changed requires a baseline snapshot"
        changed = bool(compare_snapshots(before, after)["state_changed"])
        return changed, "state changed" if changed else "state is unchanged"
    if condition in {"element_present", "element_absent"}:
        if not query:
            return False, f"{condition} requires query"
        present = query.casefold() in _state_haystack(state)
        satisfied = present if condition == "element_present" else not present
        return satisfied, f"{query!r} is {'present' if present else 'absent'}"
    if condition == "value_equals":
        if value is None:
            return False, "value_equals requires value"
        for element in state.get("elements", []):
            if not isinstance(element, dict):
                continue
            label = " ".join(str(element.get(key) or "") for key in ("label", "role", "identifier"))
            if query and query.casefold() not in label.casefold():
                continue
            if str(element.get("value")) == value:
                return True, f"matched value {value!r}"
        return False, f"no matching element has value {value!r}"
    raise CuaError(f"unsupported condition: {condition}")


def pixel_coordinate(value: float, size: int, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise CuaError(f"{field} must be a finite number")
    if not 0 <= float(value) < size:
        raise CuaError(f"{field}={value} is outside the screenshot range [0, {size})")
    return float(value)


def relative_coordinate(value: float, size: int, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise CuaError(f"{field} must be a finite number")
    if not 0 <= float(value) <= 1000:
        raise CuaError(f"{field} must be between 0 and 1000 in relative coordinate mode")
    return float(value) * (size - 1) / 1000


def coordinate_space_name() -> str:
    return "relative" if CUA_COORDINATE_MODE == "relative" else "pixel"


def coordinate_contract(width: int, height: int, snapshot_id: str) -> dict[str, Any]:
    common: dict[str, Any] = {
        "name": coordinate_space_name(),
        "mode": CUA_COORDINATE_MODE,
        "snapshot_binding": snapshot_id,
        "rule": "Coordinates are valid only with this snapshot_id; re-observe after every action.",
    }
    if CUA_COORDINATE_MODE == "relative":
        return {
            **common,
            "x_min": 0,
            "x_max_inclusive": 1000,
            "y_min": 0,
            "y_max_inclusive": 1000,
            "mapped_png_width": width,
            "mapped_png_height": height,
        }
    return {
        **common,
        "x_min": 0,
        "x_max_exclusive": width,
        "y_min": 0,
        "y_max_exclusive": height,
    }


def coordinate_instruction(width: int, height: int, snapshot_id: str) -> str:
    prefix = f"Authoritative encoded-PNG metadata for {snapshot_id}: H×W={height}×{width} px; "
    if CUA_COORDINATE_MODE == "relative":
        return (
            prefix + "use relative x,y∈[0,1000]. The server maps that normalized frame to this PNG; "
            "client- or model-side image resizing does not change the coordinate range."
        )
    return (
        prefix + f"use absolute x∈[0,{width}), y∈[0,{height}). A client or model pipeline may resize "
        "the displayed image; do not infer coordinates from its rendered dimensions."
    )


def coordinate_to_pixel(value: float, size: int, field: str) -> float:
    if CUA_COORDINATE_MODE == "relative":
        return relative_coordinate(value, size, field)
    return pixel_coordinate(value, size, field)


def convert_point(record: SnapshotRecord, x: float, y: float) -> tuple[float, float]:
    if not record.frame_valid or record.width is None or record.height is None:
        raise CuaError(
            "screenshot coordinates are unavailable because the latest screenshot frame was not proven; "
            "re-observe on a visible ordinary window"
        )
    return coordinate_to_pixel(x, record.width, "x"), coordinate_to_pixel(y, record.height, "y")


def element_center_pixels(record: SnapshotRecord, element_token: str) -> tuple[float, float] | None:
    if not record.frame_valid or record.width is None or record.height is None:
        return None
    element = next(
        (item for item in record.state.get("elements", []) if item.get("element_token") == element_token),
        None,
    )
    if not isinstance(element, dict) or not isinstance(element.get("frame"), dict):
        return None
    frame = element["frame"]
    bounds = record.state.get("window_bounds") or record.target.window.get("bounds")
    if not isinstance(bounds, dict):
        return None
    window_w, window_h = _number(bounds.get("width")), _number(bounds.get("height"))
    if window_w <= 0 or window_h <= 0:
        return None
    center_x = _number(frame.get("x")) + _number(frame.get("w")) / 2 - _number(bounds.get("x"))
    center_y = _number(frame.get("y")) + _number(frame.get("h")) / 2 - _number(bounds.get("y"))
    pixel_x = center_x * record.width / window_w
    pixel_y = center_y * record.height / window_h
    if 0 <= pixel_x < record.width and 0 <= pixel_y < record.height:
        return pixel_x, pixel_y
    return None


def snapshot_id_from_token(token: str | None) -> str | None:
    if not token:
        return None
    match = _SNAPSHOT_RE.match(token)
    return match.group(1) if match else None


def reset_runtime_for_tests() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None
    SNAPSHOTS.clear()
