"""Qwen-MM-Plugins CUA — profile-based grounded control over Cua Driver."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp_framework import build_registry
from qwen_mm_plugins_cua.mode import CUA_COORDINATE_MODE, CUA_TYPE

__version__ = "2.2.0"

if CUA_TYPE == "native":
    _packages = ["native_tools"]
else:
    _packages = ["tools"]
if CUA_TYPE == "full":
    _packages.extend(["browser_tools", "runtime_tools"])

SPECS, _, _ = build_registry(__name__, _packages)
_HANDLERS = {spec.name: spec.handle for spec in SPECS}


def get_handler(name: str):
    return _HANDLERS.get(name)


def list_tools() -> list[dict]:
    return [spec.meta for spec in SPECS]


_DRIVER_TOOLS = ["cua-driver", "cua-driver-local"]
if sys.platform == "darwin":
    _DRIVER_TOOLS.extend(
        [
            str(Path.home() / ".local" / "bin" / "cua-driver"),
            str(Path.home() / ".local" / "bin" / "cua-driver-local"),
            "/Applications/CuaDriver.app/Contents/MacOS/cua-driver",
            "/Applications/CuaDriverLocal.app/Contents/MacOS/cua-driver-local",
        ]
    )

SYSTEM_DEPS = [
    {
        "label": "Cua Driver 0.20.0+",
        "tools": _DRIVER_TOOLS,
        "hint": "Install the matching Driver build; see cookbooks/cua/usage.md",
    }
]

SYSTEM_DEPS_NOTE = (
    f"Active CUA profile: {CUA_TYPE}; screenshot coordinate mode: {CUA_COORDINATE_MODE}. "
    "Set QWEN_MM_CUA_TYPE=native, ax, or full and QWEN_MM_CUA_COORDINATE_MODE=absolute or relative "
    "in the shared config. Apply changes with the client's plugin/MCP reload action or a new session. "
    "The full profile exposes a curated Browser/Runtime subset, never the "
    "driver's administrative roster. On macOS, verify Accessibility and Screen Recording with "
    "`cua-driver permissions status`; on Windows/Linux, use `cua-driver doctor` from the interactive "
    "desktop session."
)

USAGE_NOTE = (
    "Requires Cua Driver 0.20.0 or newer. The runtime is resolved from "
    "QWEN_MM_CUA_DRIVER_PATH, PATH, ~/.local/bin/cua-driver, then the macOS app bundle. "
    f"Active profile: {CUA_TYPE}; screenshot coordinate mode: {CUA_COORDINATE_MODE}. "
    "See the CUA cookbook for Driver installation and the bundled Skill for the snapshot loop."
)
