"""Qwen-MM-Plugins CUA — a first-party MCP proxy for the external Cua Driver.

The Cua Driver owns platform permissions and the computer-use implementation.  This package owns
the stable Qwen-MM-Plugins entry point, resolves the driver's installation location in GUI and
terminal hosts alike, and transparently forwards the MCP protocol without duplicating Cua's tool
schemas.
"""

from mcp_framework import __version__ as __version__
