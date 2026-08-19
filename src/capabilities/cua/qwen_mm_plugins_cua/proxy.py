"""Resolve QwenLM's ``open-computer-use`` and proxy its stdio MCP transport.

The upstream runtime owns the screenshot-first computer-use implementation. This package owns its
discovery and the stable Qwen MCP server identity.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO, Sequence

from qwen_mm_plugins_cua import __version__ as PLUGIN_VERSION
from shared.env import get_env

SERVER_NAME = "qwen-mm-plugins-cua"
OPEN_COMPUTER_USE_PACKAGE = "@qwen-code/open-computer-use@0.2.3"
_OPEN_COMPUTER_USE_ENV_KEY = "QWEN_MM_OPEN_COMPUTER_USE_PATH"
_GLOBAL_POINTER_CONFIG_KEY = "QWEN_MM_CUA_GLOBAL_POINTER_FALLBACKS"
_GLOBAL_POINTER_ENV_KEY = "OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})
_COORDINATE_MAX = 1000.0
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COORDINATE_FIELDS = {
    "click": (("x", "x"), ("y", "y")),
    "drag": (("from_x", "x"), ("from_y", "y"), ("to_x", "x"), ("to_y", "y")),
}


class ProxyState:
    """Track the screenshot that coordinate actions are relative to."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending_tools: dict[str, tuple[str, str]] = {}
        self._screenshot_sizes: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _app_key(app: str) -> str:
        return app.strip().casefold()

    @staticmethod
    def _request_key(request_id: object) -> str:
        return f"{type(request_id).__name__}:{json.dumps(request_id, ensure_ascii=False, sort_keys=True)}"

    def track_tool_request(self, request_id: object, tool_name: str, app: str) -> None:
        with self._lock:
            self._pending_tools[self._request_key(request_id)] = (tool_name, app)

    def pop_tool_request(self, request_id: object) -> tuple[str, str] | None:
        with self._lock:
            return self._pending_tools.pop(self._request_key(request_id), None)

    def screenshot_size(self, app: str) -> tuple[int, int] | None:
        with self._lock:
            return self._screenshot_sizes.get(self._app_key(app))

    def update_screenshot_size(self, app: str, size: tuple[int, int] | None) -> None:
        key = self._app_key(app)
        with self._lock:
            if size is None:
                self._screenshot_sizes.pop(key, None)
            else:
                self._screenshot_sizes[key] = size

    def clear_screenshot_sizes(self) -> None:
        with self._lock:
            self._screenshot_sizes.clear()


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_open_computer_use(*, which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """Return a command that starts open-computer-use's stdio MCP server.

    A direct executable wins for managed installations. Otherwise npx downloads or reuses the pinned
    upstream package at first launch, avoiding a host-specific global npm prefix in the manifest.
    """
    if configured := get_env(_OPEN_COMPUTER_USE_ENV_KEY):
        path = Path(configured).expanduser()
        if _is_executable(path):
            return [str(path), "mcp"]
        raise RuntimeError(f"{_OPEN_COMPUTER_USE_ENV_KEY} is not an executable file: {path}")

    if npx := which("npx"):
        return [npx, "--yes", f"--package={OPEN_COMPUTER_USE_PACKAGE}", "open-computer-use", "mcp"]
    if executable := which("open-computer-use"):
        return [executable, "mcp"]
    return None


def open_computer_use_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Map the Qwen safety switch to the upstream runtime environment."""
    environment = dict(os.environ if base is None else base)
    configured = get_env(_GLOBAL_POINTER_CONFIG_KEY)
    if configured is None:
        return environment

    normalized = configured.strip().lower()
    if normalized in _TRUE_VALUES:
        environment[_GLOBAL_POINTER_ENV_KEY] = "1"
    elif normalized in _FALSE_VALUES:
        environment[_GLOBAL_POINTER_ENV_KEY] = "0"
    else:
        accepted = "on/off, true/false, yes/no, or 1/0"
        raise RuntimeError(f"{_GLOBAL_POINTER_CONFIG_KEY} must be {accepted}; got {configured!r}")
    return environment


def check_system() -> str:
    """Render a focused dependency check suitable for CI and the guided installer."""
    try:
        command = resolve_open_computer_use()
        environment = open_computer_use_environment()
    except RuntimeError as exc:
        return f"✗ open-computer-use\n    {exc}"
    if command is None:
        return (
            "✗ open-computer-use\n"
            "    install Node.js (npm/npx), or set QWEN_MM_OPEN_COMPUTER_USE_PATH=/absolute/path/to/open-computer-use"
        )
    source = "npx package (downloaded on first launch)" if Path(command[0]).stem == "npx" else "executable"
    global_pointer = environment.get(_GLOBAL_POINTER_ENV_KEY, "").strip().lower() in _TRUE_VALUES
    fallback_status = "enabled (may activate the app and move the pointer)" if global_pointer else "disabled"
    return (
        f"✓ open-computer-use\n"
        f"    {source}: {command[0]}\n"
        f"    MCP tools: 9 (screenshot-first)\n"
        f"    global pointer fallback: {fallback_status}"
    )


def rewrite_initialize_response(line: bytes) -> bytes:
    """Replace the upstream identity and add the proxy's coordinate convention.

    MCP stdio uses one JSON-RPC message per line.  Any malformed/non-JSON line is forwarded exactly
    so an upstream transport extension cannot corrupt the connection.
    """
    try:
        payload = json.loads(line)
        server_info = payload.get("result", {}).get("serverInfo")
        if not isinstance(server_info, dict):
            return line
        server_info["name"] = SERVER_NAME
        server_info["version"] = PLUGIN_VERSION
        instructions = payload.get("result", {}).get("instructions")
        coordinate_note = (
            "Coordinate actions exposed by this server use relative values from 0 to 1000 on the "
            "latest screenshot: (0, 0) is top-left and (1000, 1000) is bottom-right."
        )
        if isinstance(instructions, str) and coordinate_note not in instructions:
            payload["result"]["instructions"] = f"{instructions.rstrip()}\n\n{coordinate_note}"
    except (AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return line
    ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + ending


def _line_ending(line: bytes) -> bytes:
    return b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""


def _encode_line(payload: object, original: bytes) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + _line_ending(original)


def _png_size(result: object) -> tuple[int, int] | None:
    """Read PNG dimensions from an MCP image content block without decoding the full image."""
    if not isinstance(result, dict) or result.get("isError") is True:
        return None
    content = result.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        if item.get("mimeType") not in (None, "image/png"):
            continue
        data = item.get("data")
        if not isinstance(data, str):
            continue
        try:
            header = base64.b64decode(data[:32], validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(header) < 24 or header[:8] != _PNG_SIGNATURE or header[12:16] != b"IHDR":
            continue
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        if width > 0 and height > 0:
            return width, height
    return None


def _relative_to_pixel(value: object, size: int, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number from 0 to 1000")
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{field} must be a finite number from 0 to 1000") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be a finite number from 0 to 1000")
    if not 0 <= numeric <= _COORDINATE_MAX:
        raise ValueError(f"{field} must be between 0 and 1000")
    # Upstream treats screenshot coordinates as continuous values. Scale against the last valid
    # pixel so both inclusive endpoints map exactly inside the image.
    return numeric * (size - 1) / _COORDINATE_MAX


def _rewrite_coordinate_arguments(tool_name: str, arguments: dict, state: ProxyState) -> bool:
    fields = _COORDINATE_FIELDS.get(tool_name)
    if fields is None or (tool_name == "click" and arguments.get("element_index") is not None):
        return False

    present = [field in arguments and arguments[field] is not None for field, _ in fields]
    if tool_name == "click" and not any(present):
        return False
    if not all(present):
        names = ", ".join(field for field, _ in fields)
        raise ValueError(f"{tool_name} relative coordinates require {names}")

    app = arguments.get("app")
    if not isinstance(app, str) or not app.strip():
        raise ValueError(f"{tool_name} relative coordinates require a non-empty app")
    size = state.screenshot_size(app)
    if size is None:
        raise ValueError(
            f"No screenshot dimensions are available for {app!r}. Call get_app_state and ensure "
            "it returns a screenshot before using relative coordinates; element_index does not "
            "require a screenshot."
        )

    width, height = size
    for field, axis in fields:
        arguments[field] = _relative_to_pixel(arguments[field], width if axis == "x" else height, field)
    return True


def _tool_error_response(request_id: object, message: str, original: bytes) -> bytes:
    return _encode_line(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": f"{SERVER_NAME}: {message}"}],
                "isError": True,
            },
        },
        original,
    )


def rewrite_client_request(line: bytes, state: ProxyState) -> tuple[bytes | None, bytes | None]:
    """Convert relative action coordinates before forwarding a client request upstream."""
    try:
        payload = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return line, None
    if not isinstance(payload, dict):
        return line, None

    if payload.get("method") == "notifications/turn-ended":
        state.clear_screenshot_sizes()
        return line, None
    if payload.get("method") != "tools/call":
        return line, None

    params = payload.get("params")
    if not isinstance(params, dict):
        return line, None
    tool_name = params.get("name")
    arguments = params.get("arguments")
    if not isinstance(tool_name, str) or not isinstance(arguments, dict):
        return line, None

    try:
        changed = _rewrite_coordinate_arguments(tool_name, arguments, state)
    except ValueError as exc:
        if "id" not in payload:
            return None, None
        return None, _tool_error_response(payload["id"], str(exc), line)

    app = arguments.get("app")
    if "id" in payload and isinstance(app, str):
        state.track_tool_request(payload["id"], tool_name, app)
    return (_encode_line(payload, line) if changed else line), None


def _relative_coordinate_property(description: str) -> dict:
    return {
        "type": "number",
        "minimum": 0,
        "maximum": 1000,
        "description": description,
    }


def _rewrite_tool_schemas(tools: object) -> bool:
    if not isinstance(tools, list):
        return False
    changed = False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        schema = tool.get("inputSchema")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict):
            continue
        if name == "click":
            tool["description"] = (
                "Click an element by index or relative coordinates on the latest screenshot. "
                "This tool is part of plugin `Computer Use`."
            )
            properties["x"] = _relative_coordinate_property(
                "Relative X coordinate from 0 (left) to 1000 (right) on the latest screenshot"
            )
            properties["y"] = _relative_coordinate_property(
                "Relative Y coordinate from 0 (top) to 1000 (bottom) on the latest screenshot"
            )
            schema["anyOf"] = [{"required": ["element_index"]}, {"required": ["x", "y"]}]
            changed = True
        elif name == "drag":
            tool["description"] = (
                "Drag between two relative points on the latest screenshot. Coordinates range "
                "from 0 to 1000. This tool is part of plugin `Computer Use`."
            )
            properties["from_x"] = _relative_coordinate_property("Start X from 0 (left) to 1000 (right)")
            properties["from_y"] = _relative_coordinate_property("Start Y from 0 (top) to 1000 (bottom)")
            properties["to_x"] = _relative_coordinate_property("End X from 0 (left) to 1000 (right)")
            properties["to_y"] = _relative_coordinate_property("End Y from 0 (top) to 1000 (bottom)")
            changed = True
        elif name == "get_app_state":
            description = tool.get("description")
            note = " Coordinate actions use 0–1000 relative coordinates on the returned screenshot."
            if isinstance(description, str) and note.strip() not in description:
                tool["description"] = description.rstrip() + note
                changed = True
    return changed


def rewrite_server_response(line: bytes, state: ProxyState) -> bytes:
    """Rebrand the server, advertise relative schemas, and remember screenshot dimensions."""
    rewritten = rewrite_initialize_response(line)
    try:
        payload = json.loads(rewritten)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return rewritten
    if not isinstance(payload, dict):
        return rewritten

    changed = rewritten != line
    result = payload.get("result")
    if isinstance(result, dict) and _rewrite_tool_schemas(result.get("tools")):
        changed = True

    pending = state.pop_tool_request(payload["id"]) if "id" in payload else None
    if pending is not None:
        _, app = pending
        state.update_screenshot_size(app, _png_size(result))

    return _encode_line(payload, line) if changed else line


def _write_stream(target: BinaryIO, data: bytes, lock: threading.Lock) -> None:
    with lock:
        target.write(data)
        target.flush()


def _copy_input(
    source: BinaryIO,
    target: BinaryIO,
    client_output: BinaryIO,
    state: ProxyState,
    client_write_lock: threading.Lock,
) -> None:
    try:
        while line := source.readline():
            upstream_line, local_response = rewrite_client_request(line, state)
            if local_response is not None:
                _write_stream(client_output, local_response, client_write_lock)
            if upstream_line is not None:
                target.write(upstream_line)
                target.flush()
    except BrokenPipeError:
        pass
    finally:
        try:
            target.close()
        except BrokenPipeError:
            pass


def _copy_stream(source: BinaryIO, target: BinaryIO) -> None:
    try:
        shutil.copyfileobj(source, target)
        target.flush()
    except BrokenPipeError:
        pass


def run_proxy(command: Sequence[str], *, base_environment: Mapping[str, str] | None = None) -> int:
    """Run the upstream command and bridge its stdio transport to this process."""
    environment = open_computer_use_environment(base_environment)
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        bufsize=0,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    state = ProxyState()
    client_write_lock = threading.Lock()
    input_thread = threading.Thread(
        target=_copy_input,
        args=(sys.stdin.buffer, process.stdin, sys.stdout.buffer, state, client_write_lock),
        daemon=True,
    )
    stderr_thread = threading.Thread(target=_copy_stream, args=(process.stderr, sys.stderr.buffer), daemon=True)
    input_thread.start()
    stderr_thread.start()
    try:
        while line := process.stdout.readline():
            _write_stream(sys.stdout.buffer, rewrite_server_response(line, state), client_write_lock)
    except BrokenPipeError:
        process.terminate()
    finally:
        input_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
    return process.wait()
