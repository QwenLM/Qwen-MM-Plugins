"""Smoke + protocol tests for the freecad capability's MCP server.

conftest auto-discovers qwen_mm_plugins_freecad. Like the blender server it is a THIN
CLIENT: handlers talk XML-RPC to a running FreeCAD + FreeCADMCP addon. No live FreeCAD in
CI, so we exercise discovery + schema/handler surface, graceful degradation with nothing
listening, and the MCP stdio bridge. Live CAD (real execute_code / views / FEM) is Phase C,
run manually per the skill.
"""

import os
import threading
import time

from conftest import REPO_ROOT, mcp_call

import qwen_mm_plugins_freecad as m

FREECAD_SERVER_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "freecad", "qwen_mm_plugins_freecad")

# The full trained tool surface (freecad-mcp), ported 1:1. Doubles as a drift guard.
FREECAD_TOOLS = {
    "create_document",
    "list_documents",
    "reload_document",
    "create_object",
    "edit_object",
    "delete_object",
    "execute_code",
    "execute_code_async",
    "get_objects",
    "get_object",
    "get_view",
    "get_parts_list",
    "insert_part_from_library",
    "run_fem_analysis",
}

# Point the loader at a port nothing is listening on (default is localhost:9875).
_ENV = {**os.environ, "FREECAD_RPC_HOST": "127.0.0.1", "FREECAD_RPC_PORT": "59998"}


# ── surface (in-process) ─────────────────────────────────────────────


def test_lists_the_full_trained_surface():
    assert {t["name"] for t in m.list_tools()} == FREECAD_TOOLS


def test_every_tool_has_schema_and_handler():
    for tool in m.list_tools():
        assert tool["inputSchema"]["type"] == "object"
        assert callable(m.get_handler(tool["name"]))


def test_unknown_tool_has_no_handler():
    assert m.get_handler("does_not_exist") is None


# ── graceful degradation (no live FreeCAD) ───────────────────────────


def test_execute_code_degrades_gracefully(monkeypatch):
    monkeypatch.setenv("FREECAD_RPC_HOST", "127.0.0.1")
    monkeypatch.setenv("FREECAD_RPC_PORT", "59998")
    import qwen_mm_plugins_freecad.loader as loader

    loader._connection = None
    content = m.get_handler("execute_code")({"code": "print(1)"})
    assert content and content[0]["type"] == "text"
    assert "connect" in content[0]["text"].lower() or "freecad" in content[0]["text"].lower()


# ── protocol (real MCP client over stdio) ────────────────────────────


def test_server_tools_list_matches_in_process():
    result = mcp_call(FREECAD_SERVER_DIR, lambda s: s.list_tools(), env=_ENV)
    assert {t.name for t in result.tools} == {t["name"] for t in m.list_tools()}


def test_server_execute_code_call_degrades():
    result = mcp_call(
        FREECAD_SERVER_DIR,
        lambda s: s.call_tool("execute_code", {"code": "print(1)"}),
        env=_ENV,
    )
    assert any(getattr(b, "type", None) == "text" for b in result.content)


# ── regression: graceful degradation + concurrency (blcad review fixes) ──


def test_parts_list_and_list_documents_degrade_gracefully(monkeypatch):
    """get_parts_list and list_documents must return an informative text block (not raise) when
    no FreeCAD is listening, like their siblings (blcad review, finding 8)."""
    monkeypatch.setenv("FREECAD_RPC_HOST", "127.0.0.1")
    monkeypatch.setenv("FREECAD_RPC_PORT", "59998")
    import qwen_mm_plugins_freecad.loader as loader

    for tool in ("get_parts_list", "list_documents"):
        loader._connection = None
        content = m.get_handler(tool)({})
        assert content and content[0]["type"] == "text", tool
        assert "freecad" in content[0]["text"].lower() or "fail" in content[0]["text"].lower(), tool
    loader._connection = None


def test_concurrent_rpc_calls_serialize(monkeypatch):
    """Concurrent RPC calls must not race on the shared (non-thread-safe) ServerProxy transport
    (blcad review, finding 3). Uses a threaded XML-RPC mock so the server itself doesn't serialize."""
    from socketserver import ThreadingMixIn
    from xmlrpc.server import SimpleXMLRPCServer

    class _ThreadingXMLRPC(ThreadingMixIn, SimpleXMLRPCServer):
        daemon_threads = True

    srv = _ThreadingXMLRPC(("127.0.0.1", 0), allow_none=True, logRequests=False)
    port = srv.server_address[1]
    srv.register_function(lambda: True, "ping")

    def echo(code):
        time.sleep(0.02)  # widen the interleaving window
        return code

    srv.register_function(echo, "execute_code")
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    monkeypatch.setenv("FREECAD_RPC_HOST", "127.0.0.1")
    monkeypatch.setenv("FREECAD_RPC_PORT", str(port))
    import qwen_mm_plugins_freecad.loader as loader

    loader._connection = None
    try:
        conn = loader.get_connection()
        results: dict[int, str] = {}
        errors: list[str] = []

        def call(i):
            try:
                results[i] = conn.execute_code(f"tag{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

        threads = [threading.Thread(target=call, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert not errors, errors
        assert all(results.get(i) == f"tag{i}" for i in range(8)), results
    finally:
        srv.shutdown()
        srv.server_close()
        loader._connection = None
