"""Connection singleton for the FreeCAD MCP tools.

A thin XML-RPC client to a *running* FreeCAD + FreeCADMCP addon (default localhost:9875). Ported
from freecad-mcp (MIT, © neka-nat). Every tool calls get_connection(); the wire methods are 1:1
with the FreeCADMCP addon's RPC server.
"""

from __future__ import annotations

import logging
import threading
import xmlrpc.client

from shared.env import get_env, get_int_env

log = logging.getLogger("qwen-mm-plugins-freecad")

_connection: "FreeCADConnection | None" = None
_lock = threading.Lock()


class _TimeoutTransport(xmlrpc.client.Transport):
    """XML-RPC transport with a socket timeout, so a frozen FreeCAD GUI can't hang the client forever."""

    def __init__(self, timeout: float = 30, **kwargs):
        super().__init__(**kwargs)
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class FreeCADConnection:
    def __init__(self, host: str = "localhost", port: int = 9875, timeout: float = 360):
        self.server = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}",
            allow_none=True,
            transport=_TimeoutTransport(timeout=timeout),
        )
        # Serializes RPC calls: xmlrpc.client.ServerProxy shares one Transport (not thread-safe),
        # and the framework runs tool handlers in a worker-thread pool.
        self._io_lock = threading.Lock()

    def _call(self, method: str, *args):
        """Invoke an addon RPC by name, held under _io_lock so concurrent tool calls can't race
        on the shared Transport."""
        with self._io_lock:
            return getattr(self.server, method)(*args)

    def ping(self) -> bool:
        return self._call("ping")

    # --- wire methods (1:1 with the FreeCADMCP addon RPC) ---
    def create_document(self, name):
        return self._call("create_document", name)

    def create_object(self, doc_name, obj_data):
        return self._call("create_object", doc_name, obj_data)

    def edit_object(self, doc_name, obj_name, obj_data):
        return self._call("edit_object", doc_name, obj_name, obj_data)

    def delete_object(self, doc_name, obj_name):
        return self._call("delete_object", doc_name, obj_name)

    def reload_document(self, doc_name):
        return self._call("reload_document", doc_name)

    def insert_part_from_library(self, relative_path):
        return self._call("insert_part_from_library", relative_path)

    def execute_code(self, code):
        return self._call("execute_code", code)

    def execute_code_async(self, code):
        return self._call("execute_code_async", code)

    def get_objects(self, doc_name):
        return self._call("get_objects", doc_name)

    def get_object(self, doc_name, obj_name):
        return self._call("get_object", doc_name, obj_name)

    def get_parts_list(self):
        return self._call("get_parts_list")

    def list_documents(self):
        return self._call("list_documents")

    def run_fem_analysis(self, doc_name, analysis_name, timeout=600):
        return self._call("run_fem_analysis", doc_name, analysis_name, timeout)

    def get_active_screenshot(self, view_name="Isometric", width=None, height=None, focus_object=None):
        try:
            return self._call("get_active_screenshot", view_name, width, height, focus_object)
        except Exception as e:
            log.error("Error getting screenshot: %s", e)
            return None


def only_text_feedback() -> bool:
    """When FREECAD_ONLY_TEXT_FEEDBACK is set, tools skip the attached screenshot."""
    return (get_env("FREECAD_ONLY_TEXT_FEEDBACK") or "").lower() in ("1", "true", "yes")


_autolaunch_tried = False


def _maybe_autolaunch(host: str, port: int) -> bool:
    """If QWEN_MM_AUTOLAUNCH is set (and the target is local), install the bundled addon + bring up
    FreeCAD exactly once. Returns whether the port is open afterwards."""
    global _autolaunch_tried
    from shared.applaunch import autolaunch_enabled, is_local_host, launch_and_probe

    if _autolaunch_tried or not autolaunch_enabled() or not is_local_host(host):
        return False
    _autolaunch_tried = True
    from . import launch_app

    return launch_and_probe(host, port, launch_app, app="FreeCAD")


def get_connection() -> FreeCADConnection:
    """Get or create the persistent FreeCAD connection (pings once on creation)."""
    global _connection
    if _connection is not None:
        return _connection
    with _lock:
        if _connection is not None:
            return _connection
        host = get_env("FREECAD_RPC_HOST", "localhost")
        port = get_int_env("FREECAD_RPC_PORT", 9875)
        conn = FreeCADConnection(host=host, port=port)
        try:
            ok = conn.ping()
        except Exception:
            ok = False
        if not ok and _maybe_autolaunch(host, port):
            conn = FreeCADConnection(host=host, port=port)
            try:
                ok = conn.ping()
            except Exception:
                ok = False
        if not ok:
            from shared.applaunch import autolaunch_display_hint

            hint = autolaunch_display_hint()
            extra = f" {hint}." if hint else ""
            raise Exception(
                f"Could not connect to FreeCAD at {host}:{port}.{extra} Start it with "
                "`qwen-mm-plugins-freecad --launch-app` (ships the addon and auto-downloads FreeCAD "
                "if missing), or set QWEN_MM_AUTOLAUNCH=1 to do that automatically on first connect."
            )
        _connection = conn
        return _connection


def probe() -> None:
    """Fast, non-blocking connectivity check at server startup (delegated to shared.applaunch).

    Deliberately does NOT connect or autolaunch: launching FreeCAD can take tens of seconds, and
    on_start() runs before the MCP initialize handshake. Autolaunch is deferred to the first tool
    call (get_connection, in a worker thread) where a long wait is survivable."""
    from shared.applaunch import startup_probe

    host = get_env("FREECAD_RPC_HOST", "localhost")
    port = get_int_env("FREECAD_RPC_PORT", 9875)
    startup_probe(host, port, app="FreeCAD", entry="qwen-mm-plugins-freecad")
