"""Qwen-MM-Plugins CUA — a first-party MCP proxy for QwenLM/open-computer-use.

The external runtime owns platform permissions and the computer-use implementation. This package
owns the stable Qwen-MM-Plugins entry point and transparently forwards the MCP protocol without
duplicating upstream tool schemas.
"""

__version__ = "1.0.0"
