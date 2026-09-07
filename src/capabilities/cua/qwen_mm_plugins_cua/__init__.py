"""Qwen-MM-Plugins CUA — profile-based grounded control over Cua Driver."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp_framework import build_registry
from qwen_mm_plugins_cua.mode import CUA_TYPE

__version__ = "2.1.0"

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


_DRIVER_TOOLS = ["cua-driver"]
if sys.platform == "darwin":
    _DRIVER_TOOLS.extend(
        [
            str(Path.home() / ".local" / "bin" / "cua-driver"),
            "/Applications/CuaDriver.app/Contents/MacOS/cua-driver",
        ]
    )

SYSTEM_DEPS = [
    {
        "label": "Cua Driver 0.20.0+",
        "tools": _DRIVER_TOOLS,
        "hint": '/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"',
    }
]

SYSTEM_DEPS_NOTE = (
    f"Active CUA profile: {CUA_TYPE}. Set QWEN_MM_CUA_TYPE=native, ax, or full in the shared config, "
    "then restart the MCP server. The full profile exposes a curated Browser/Runtime subset, never the "
    "driver's administrative roster. On macOS, verify Accessibility and Screen Recording with "
    "`cua-driver permissions status`; on Windows/Linux, use `cua-driver doctor` from the interactive "
    "desktop session."
)

USAGE_NOTE = (
    "Requires Cua Driver 0.20.0 or newer. The runtime is resolved from "
    "QWEN_MM_CUA_DRIVER_PATH, PATH, ~/.local/bin/cua-driver, then the macOS app bundle. "
    f"Active profile: {CUA_TYPE}. "
    "Install from https://cua.ai/driver/install.sh and see the bundled Skill for the snapshot loop."
)
