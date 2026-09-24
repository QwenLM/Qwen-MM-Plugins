"""Connection singleton for the Blender MCP tools.

A thin TCP client to a *running* Blender + blender-mcp addon (default localhost:9876). Ported from
blender-mcp (MIT, © Siddharth Ahuja); telemetry removed. Every tool calls get_connection().
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from dataclasses import dataclass, field
from typing import Any

from shared.env import get_env, get_int_env

log = logging.getLogger("qwen-mm-plugins-blender")

_SOCK_TIMEOUT = 180.0  # match the addon's timeout

_connection: "BlenderConnection | None" = None
_lock = threading.Lock()


@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket | None = None
    # Serializes send_command: the framework runs tool handlers in a worker-thread pool, and the
    # single shared socket can't have two send/recv pairs interleaved (would misroute responses).
    _io_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def connect(self) -> bool:
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            log.info("Connected to Blender at %s:%s", self.host, self.port)
            return True
        except Exception as e:
            log.error("Failed to connect to Blender: %s", e)
            self.sock = None
            return False

    def _receive_full_response(self, sock, buffer_size: int = 8192) -> bytes:
        """Receive one complete JSON object, possibly across multiple chunks."""
        chunks: list[bytes] = []
        sock.settimeout(_SOCK_TIMEOUT)
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break
                    chunks.append(chunk)
                    try:
                        data = b"".join(chunks)
                        json.loads(data.decode("utf-8"))
                        return data
                    except json.JSONDecodeError:
                        continue
                except socket.timeout:
                    log.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    log.error("Socket connection error during receive: %s", e)
                    raise
        except socket.timeout:
            log.warning("Socket timeout during chunked receive")
        except Exception as e:
            log.error("Error during receive: %s", e)
            raise
        if chunks:
            data = b"".join(chunks)
            try:
                json.loads(data.decode("utf-8"))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")
        raise Exception("No data received")

    def send_command(self, command_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one command to Blender and return its `result` dict (raises on error).

        Held under `_io_lock` so concurrent tool calls (framework worker threads) can't interleave
        their send/recv on the single shared socket."""
        with self._io_lock:
            if not self.sock and not self.connect():
                raise ConnectionError("Not connected to Blender")
            command = {"type": command_type, "params": params or {}}
            try:
                self.sock.sendall(json.dumps(command).encode("utf-8"))
                self.sock.settimeout(_SOCK_TIMEOUT)
                response_data = self._receive_full_response(self.sock)
                response = json.loads(response_data.decode("utf-8"))
                if response.get("status") == "error":
                    raise Exception(response.get("message", "Unknown error from Blender"))
                return response.get("result", {})
            except socket.timeout:
                self.sock = None
                raise Exception(
                    "Timeout waiting for Blender response - try simplifying your request. If Blender is "
                    "running headless (blender -b), commands never execute; run Blender with a GUI or via "
                    "'xvfb-run -a blender' instead"
                )
            except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                self.sock = None
                raise Exception(f"Connection to Blender lost: {e}")
            except Exception:
                self.sock = None
                raise


_autolaunch_tried = False


def _maybe_autolaunch(host: str, port: int) -> bool:
    """If QWEN_MM_AUTOLAUNCH is set (and the target is local), bring up Blender + the bundled addon
    exactly once. Returns whether the port is open afterwards."""
    global _autolaunch_tried
    from shared.applaunch import autolaunch_enabled, is_local_host, launch_and_probe

    if _autolaunch_tried or not autolaunch_enabled() or not is_local_host(host):
        return False
    _autolaunch_tried = True
    from . import launch_app

    return launch_and_probe(host, port, launch_app, app="Blender")


def get_connection() -> BlenderConnection:
    """Get or create the persistent Blender connection.

    Returns the cached connection directly (no per-call health ping): send_command reconnects a
    dropped socket on next use, so a stale connection self-heals without adding a round-trip to
    every tool call. The PolyHaven flag is refreshed lazily via refresh_polyhaven()."""
    global _connection
    if _connection is not None:
        return _connection

    with _lock:
        if _connection is not None:
            return _connection
        host = get_env("BLENDER_HOST", "localhost")
        port = get_int_env("BLENDER_PORT", 9876)
        conn = BlenderConnection(host=host, port=port)
        if not conn.connect():
            if _maybe_autolaunch(host, port):
                conn = BlenderConnection(host=host, port=port)
            if not conn.connect():
                from shared.applaunch import autolaunch_display_hint

                hint = autolaunch_display_hint()
                extra = f" {hint}." if hint else ""
                raise Exception(
                    f"Could not connect to Blender at {host}:{port}.{extra} Start it with "
                    "`qwen-mm-plugins-blender --launch-app` (ships the addon and auto-downloads "
                    "Blender if missing), or set QWEN_MM_AUTOLAUNCH=1 to do that automatically on "
                    "first connect."
                )
        _connection = conn
        return _connection


def refresh_polyhaven() -> bool:
    """Query Blender for whether the PolyHaven integration is enabled.

    Called by the PolyHaven tools (which genuinely need the status) rather than on every
    get_connection(), so ordinary tool calls don't pay for a status round-trip they never use."""
    try:
        result = get_connection().send_command("get_polyhaven_status")
        return bool(result.get("enabled", False))
    except Exception as e:
        log.warning("Could not refresh PolyHaven status: %s", e)
        return False


def probe() -> None:
    """Fast, non-blocking connectivity check at server startup (delegated to shared.applaunch).

    Deliberately does NOT connect or autolaunch: launching Blender can take tens of seconds, and
    on_start() runs before the MCP initialize handshake. Autolaunch is deferred to the first tool
    call (get_connection, in a worker thread) where a long wait is survivable."""
    from shared.applaunch import startup_probe

    host = get_env("BLENDER_HOST", "localhost")
    port = get_int_env("BLENDER_PORT", 9876)
    startup_probe(host, port, app="Blender", entry="qwen-mm-plugins-blender")
