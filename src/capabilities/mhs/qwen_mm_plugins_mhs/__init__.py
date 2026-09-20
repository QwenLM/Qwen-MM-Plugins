"""Qwen-MM-Plugins MHS capability — a Model Hardware Standard host.

MHS is to hardware what MCP is to tools: one narrow surface (discover / meta_info / read / write /
health_check / reset) that a model uses to operate any device, with everything device-specific pushed
behind an *adapter*. Adapters belong to the hardware, not to this plugin — whoever owns a camera
writes and runs its adapter. This package is the host side: it keeps the device registry, enforces
the safety limits an adapter declares, and exposes the fixed tool surface.

Adapters speak MHS-HTTP/1 (HTTP + MessagePack, see skill/references/adapter_protocol.md) and are
listed in ~/.qwen-mm-plugins/mhs-devices.json. Adding a new class of hardware means writing an
adapter and adding a line to that file; it never means changing this package.
"""

__version__ = "1.0.0"

from mcp_framework import build_registry

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

# The host uses stdlib HTTP and msgpack, with no system binaries. Real
# readiness is whether the configured adapters answer, which only the tools can tell (--check-system
# cannot probe another host); mhs_health_check is the check that matters.
SYSTEM_DEPS: list[dict] = []

USAGE_NOTE = (
    "Talks to external MHS adapters over HTTP; each adapter is owned and run by the hardware it "
    "fronts. List them in ~/.qwen-mm-plugins/mhs-devices.json (override the path with "
    "QWEN_MM_MHS_DEVICES):\n"
    '  {"adapters": [{"name": "lab-cam", "url": "http://192.168.1.20:8800"}]}\n'
    "Writing an adapter: see skill/references/adapter_protocol.md, and "
    "skill/references/mock_adapter.py for a runnable one you can copy."
)
