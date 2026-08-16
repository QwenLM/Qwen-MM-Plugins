"""Qwen-MM-Plugins proxy: a local HTTP protocol proxy that gives text-only models vision.

Standalone (non-MCP) capability: a resident HTTP server on 127.0.0.1:8787 that intercepts
images in Anthropic / Responses / Chat requests, transcribes them via a VLM, and forwards
text to the real upstream. See docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md.
"""

__version__ = "0.1.0"
