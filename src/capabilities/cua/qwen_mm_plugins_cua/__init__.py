"""Qwen-MM-Plugins CUA (computer-use) capability — drive the local desktop.

WIP / half-built: this first cut ships only `screenshot` (capture the local screen).
The action space (click / type / scroll) and packaging (pyproject entry + plugin
manifests) are the other half — see TODO.md in this capability.

Design note: computer-use drives the machine THIS server runs on. It needs a real
display, so it only works when the server runs on a local desktop (macOS first), not
on a headless/remote box. On a headless server there is no screen to capture.
"""

from mcp_framework import __version__ as __version__
from mcp_framework import build_registry

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

# Screen capture uses `mss` (pip-installable, so not a system dep). macOS additionally needs the
# host process to hold Screen Recording permission — an OS grant pip/uv can't install and that we
# can't reliably probe at startup without capturing, so we surface it at call time (the screenshot
# tool's error hint) and in the skill, rather than as a noisy unconditional startup warning.
SYSTEM_DEPS: list[dict] = []

USAGE_NOTE = "CUA computer-use tools. Runs against the LOCAL desktop — needs a real screen (macOS first)."
