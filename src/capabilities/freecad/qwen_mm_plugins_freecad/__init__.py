"""Qwen-MM-Plugins FreeCAD capability — a thin MCP client for a live FreeCAD + FreeCADMCP addon.

Ported from freecad-mcp (MIT, © neka-nat — https://github.com/neka-nat/freecad-mcp) and adapted
onto Qwen-MM-Plugins' mcp_framework. These tools are a stdio MCP server that connects over XML-RPC
to a *running* FreeCAD instance carrying the vendored FreeCADMCP addon (bundled in vendor/FreeCADMCP/,
see skill/SKILL.md). This process does NOT launch FreeCAD as part of serving — start it yourself, or
run `qwen-mm-plugins-freecad --launch-app` (copies the bundled addon into your Mod dir + launches).
"""

import sys

from mcp_framework import build_registry

__version__ = "1.0.2"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

# System tools pip/uv cannot install. NOTE: these tools are a THIN CLIENT — FreeCAD may run on
# another host. --check-system can only probe binaries on PATH (report-only, startup=False); real
# readiness is the live session, checked by on_start() / the skill.
_FREECAD_TOOLS = ["freecad", "FreeCAD"]
if sys.platform == "darwin":
    _FREECAD_TOOLS.append("/Applications/FreeCAD.app/Contents/MacOS/FreeCAD")

SYSTEM_DEPS = [
    {
        "label": "FreeCAD (1.0+; 1.1.x recommended — the host that runs the live session)",
        "tools": _FREECAD_TOOLS,
        "hint": "auto-downloaded on Linux-x86_64 by `--launch-app` / QWEN_MM_AUTOLAUNCH (pinned 1.1.x "
        "AppImage, FUSE-free); else `apt install freecad` or `brew install --cask freecad`",
        "startup": False,
    },
]
if sys.platform == "linux":
    SYSTEM_DEPS.append(
        {
            "label": "xvfb (headless display for the FreeCAD GUI)",
            "tools": ["xvfb-run"],
            "hint": "apt install xvfb (needs root — the one step auto-download can't do; skip on a real display with --gui)",
            "startup": False,
        }
    )
SYSTEM_DEPS.append(
    {
        "label": "CalculiX solver — only for run_fem_analysis",
        "tools": ["ccx", "ccx_2.21", "CalculiX"],
        "hint": "apt install calculix-ccx",
        "startup": False,
    }
)

SYSTEM_DEPS_NOTE = (
    "These tools are a THIN CLIENT: they connect to a running FreeCAD + FreeCADMCP addon at "
    "$FREECAD_RPC_HOST:$FREECAD_RPC_PORT (default localhost:9875). --check-system only checks "
    "binaries on PATH — it cannot verify the live session. Bring one up with `qwen-mm-plugins-freecad "
    "--launch-app` (installs the bundled addon + launches; add --gui on a desktop); it auto-downloads "
    "FreeCAD 1.1.x on Linux-x86_64 if it's missing (QWEN_MM_NO_AUTO_INSTALL disables that). On "
    "headless Linux, install xvfb; other platforms launch the native GUI directly. See the skill."
)

USAGE_NOTE = (
    "Connects to a running FreeCAD instance (FreeCADMCP addon) at $FREECAD_RPC_HOST:$FREECAD_RPC_PORT "
    "(default localhost:9875). Start one with `qwen-mm-plugins-freecad --launch-app` (the bundled "
    "addon ships with this package), or launch FreeCAD yourself — see SKILL.md. "
    "Set FREECAD_ONLY_TEXT_FEEDBACK=1 to disable the screenshot attached to most tool results."
)


def launch_app(argv):
    """`--launch-app`: install the bundled addon + bring up a live FreeCAD (see _launch.py)."""
    from ._launch import launch_app as _impl

    return _impl(argv)


def on_start():
    from .loader import probe

    probe()
