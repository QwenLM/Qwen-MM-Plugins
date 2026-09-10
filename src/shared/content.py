"""MCP content-block builders + input guards — the helpers almost every tool returns/validates with.

Part of the `shared` library (src/shared/); stdlib-only.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
from typing import Any


def text(msg: str) -> dict[str, str]:
    """An MCP text content block: ``{"type": "text", "text": msg}``."""
    return {"type": "text", "text": msg}


def json_text(value: Any) -> dict[str, str]:
    """A text block holding ``value`` as indented JSON, non-ASCII kept.

    The sibling of ``text``, for a tool whose answer is a record rather than a sentence. Returns the
    one block, not a list, so composing several blocks reads the same either way.
    """
    return {"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}


def image(data: bytes | str, mime: str = "image/jpeg") -> dict[str, str]:
    """An MCP image content block. ``data`` is raw bytes (base64-encoded here) or an
    already-base64-encoded ``str``; ``mime`` defaults to JPEG."""
    b64 = data if isinstance(data, str) else base64.b64encode(data).decode("ascii")
    return {"type": "image", "data": b64, "mimeType": mime}


def text_error(msg: str) -> list[dict[str, str]]:
    """The standard single-block error result: [{"type":"text","text":"Error: ..."}]."""
    return [{"type": "text", "text": f"Error: {msg}"}]


def require_file(path: str) -> list[dict[str, str]] | None:
    """Return a 'file not found' error block if `path` isn't a file, else None.

    Use as:  ``if err := require_file(p): return err``
    """
    if not path or not os.path.isfile(path):
        return text_error(f"file not found: {path}")
    return None


def require_dep(module: str, pip_name: str | None = None) -> list[dict[str, str]] | None:
    """Return a 'missing dependency' error block if `module` isn't importable, else None."""
    if importlib.util.find_spec(module) is None:
        return text_error(f"missing dependency. Install with: pip install {pip_name or module}")
    return None


def default_output_path(src: str, suffix: str) -> str:
    """Sibling path with `suffix` appended to the stem (e.g. foo.png -> foo_cropped.png)."""
    p = Path(src)
    return str(p.with_stem(p.stem + suffix))
