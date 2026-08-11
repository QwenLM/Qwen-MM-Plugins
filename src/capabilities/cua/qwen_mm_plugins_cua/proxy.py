"""Resolve Cua Driver and transparently proxy its stdio MCP transport.

Keeping this a JSON-RPC proxy instead of re-declaring Cua's tools matters: the driver owns a large,
fast-moving tool surface, so forwarding its native ``tools/list`` response prevents schema drift.
Only the initialize response is rebranded to the first-party Qwen server name.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from shared.env import get_env

SERVER_NAME = "qwen-mm-plugins-cua"
_DRIVER_ENV_KEYS = ("QWEN_MM_CUA_DRIVER_PATH", "CUA_DRIVER_PATH")


def resolve_driver(
    *, home: Path | None = None, platform: str | None = None, which: Callable[[str], str | None] = shutil.which
) -> Path | None:
    """Return the Cua Driver executable for this host, without relying on GUI PATH inheritance."""
    for key in _DRIVER_ENV_KEYS:
        if value := get_env(key):
            path = Path(value).expanduser()
            if _is_executable(path):
                return path
            raise RuntimeError(f"{key} is not an executable file: {path}")

    executable = "cua-driver.exe" if os.name == "nt" else "cua-driver"
    candidates = [(home or Path.home()) / ".local" / "bin" / executable]
    if (platform or sys.platform) == "darwin":
        candidates.append(Path("/Applications/CuaDriver.app/Contents/MacOS/cua-driver"))
    for path in candidates:
        if _is_executable(path):
            return path
    if found := which(executable):
        return Path(found)
    return None


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def check_system() -> str:
    """Render a focused dependency check suitable for CI and the guided installer."""
    try:
        driver = resolve_driver()
    except RuntimeError as exc:
        return f"✗ Cua Driver\n    {exc}"
    if driver is None:
        return (
            "✗ Cua Driver\n"
            '    install: /bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"\n'
            "    or set QWEN_MM_CUA_DRIVER_PATH=/absolute/path/to/cua-driver"
        )
    return f"✓ Cua Driver\n    executable: {driver}"


def rewrite_initialize_response(line: bytes) -> bytes:
    """Replace only the upstream server identity in one JSON-RPC stdio line.

    MCP stdio uses one JSON-RPC message per line.  Any malformed/non-JSON line is forwarded exactly
    so a future Cua Driver transport extension cannot corrupt the connection.
    """
    try:
        payload = json.loads(line)
        server_info = payload.get("result", {}).get("serverInfo")
        if not isinstance(server_info, dict):
            return line
        server_info["name"] = SERVER_NAME
    except (AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return line
    ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + ending


def _copy_input(source: BinaryIO, target: BinaryIO) -> None:
    try:
        while line := source.readline():
            target.write(line)
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


def run_proxy(driver: Path) -> int:
    """Run the driver and bridge its stdio transport to this process."""
    process = subprocess.Popen(
        [str(driver), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    input_thread = threading.Thread(target=_copy_input, args=(sys.stdin.buffer, process.stdin), daemon=True)
    stderr_thread = threading.Thread(target=_copy_stream, args=(process.stderr, sys.stderr.buffer), daemon=True)
    input_thread.start()
    stderr_thread.start()
    try:
        while line := process.stdout.readline():
            sys.stdout.buffer.write(rewrite_initialize_response(line))
            sys.stdout.buffer.flush()
    except BrokenPipeError:
        process.terminate()
    finally:
        input_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
    return process.wait()
