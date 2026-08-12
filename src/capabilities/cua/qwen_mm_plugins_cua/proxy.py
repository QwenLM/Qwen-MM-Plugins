"""Resolve QwenLM's ``open-computer-use`` and proxy its stdio MCP transport.

The upstream runtime owns the screenshot-first computer-use implementation. This package owns its
discovery and the stable Qwen MCP server identity.
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
from typing import BinaryIO, Sequence

from shared.env import get_env

SERVER_NAME = "qwen-mm-plugins-cua"
OPEN_COMPUTER_USE_PACKAGE = "@qwen-code/open-computer-use@0.2.3"
_OPEN_COMPUTER_USE_ENV_KEY = "QWEN_MM_OPEN_COMPUTER_USE_PATH"


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


def check_system() -> str:
    """Render a focused dependency check suitable for CI and the guided installer."""
    try:
        command = resolve_open_computer_use()
    except RuntimeError as exc:
        return f"✗ open-computer-use\n    {exc}"
    if command is None:
        return (
            "✗ open-computer-use\n"
            "    install Node.js (npm/npx), or set QWEN_MM_OPEN_COMPUTER_USE_PATH=/absolute/path/to/open-computer-use"
        )
    source = "npx package (downloaded on first launch)" if Path(command[0]).stem == "npx" else "executable"
    return f"✓ open-computer-use\n    {source}: {command[0]}\n    MCP tools: 9 (screenshot-first)"


def rewrite_initialize_response(line: bytes) -> bytes:
    """Replace only the upstream server identity in one JSON-RPC stdio line.

    MCP stdio uses one JSON-RPC message per line.  Any malformed/non-JSON line is forwarded exactly
    so an upstream transport extension cannot corrupt the connection.
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


def run_proxy(command: Sequence[str]) -> int:
    """Run the upstream command and bridge its stdio transport to this process."""
    process = subprocess.Popen(
        list(command),
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
