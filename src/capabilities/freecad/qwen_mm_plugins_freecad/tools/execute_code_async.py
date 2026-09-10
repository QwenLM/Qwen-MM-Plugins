"""MCP tool: execute Python inside FreeCAD in a background thread (fire-and-forget, NOT GUI-safe)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ExecuteCodeAsyncArgs(BaseModel):
    code: str


TOOL = {"name": "execute_code_async", "args": ExecuteCodeAsyncArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute Python code in FreeCAD without waiting for completion. Use this ONLY for long-running
    background computations that do NOT touch the FreeCAD GUI or mutate the FreeCAD document tree
    directly. This tool runs the submitted code in a background thread and returns immediately.
    Because it does not run on FreeCAD's main GUI thread, the code must NOT call FreeCADGui APIs,
    manipulate the active view or selection, create or edit document objects, change object
    properties, call doc.recompute(), or save documents. For code that touches FreeCAD documents,
    document objects, FreeCADGui, the active view, selection, recompute, or save operations, use
    execute_code instead — execute_code runs on the FreeCAD GUI thread and is the safe default for
    normal FreeCAD automation. Use execute_code_async only for background-safe work such as long-
    running pure OCCT geometry calculations (e.g. fuse/cut/loft on already-fetched shapes) or other
    CPU-bound computations that do not interact with the document or GUI. Typical usage pattern: (1)
    Fetch shapes into local variables first (via execute_code on the GUI thread). (2) Store
    intermediate results in a module-level Python variable (not in the FreeCAD document) so
    execute_code can read them later. (3) Run the heavy computation via execute_code_async. (4)
    After the expected computation time has elapsed, apply results to the document via execute_code
    (which runs on the GUI thread).

    Args:
        code: Background-safe Python code to execute.
    """
    from qwen_mm_plugins_freecad._responses import text_response
    from qwen_mm_plugins_freecad.loader import get_connection

    code = arguments.get("code", "")
    try:
        conn = get_connection()
        res = conn.execute_code_async(code)
        if res.get("success"):
            return text_response(
                "Code execution started in background.\n"
                "Use get_object to poll a document object for completion "
                "(e.g. check SessionState.Label). "
                "FreeCAD's Report View will show output when done."
            )
        return text_response(f"Failed to start async execution: {res.get('error', 'unknown')}")
    except Exception as e:
        return text_response(f"Failed to start async code execution: {e}")
