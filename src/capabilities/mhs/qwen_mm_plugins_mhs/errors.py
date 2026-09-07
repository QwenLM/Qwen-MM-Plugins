"""One place that turns every failure into a text block, so no tool can raise at the model.

A hardware tool that throws is worse than one that explains: the model loses the chance to check
health, back off, or tell the user which device is unreachable. Every handler is wrapped in `guarded`,
and every expected failure — unconfigured registry, unreachable adapter, unknown device, malformed
adapter response — arrives as `Error: ...` text.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from shared.content import text_error

from .config import ConfigError
from .http_client import AdapterError
from .protocol import ProtocolError

log = logging.getLogger("qwen-mm-plugins-mhs")

Handler = Callable[[dict[str, Any]], list[dict[str, Any]]]

# Failures that are part of normal operation: a device is off, a token expired, the registry has a
# typo. These carry messages already written for the model, so they pass through verbatim.
_EXPECTED = (ConfigError, AdapterError, ProtocolError, LookupError, ValueError)


def guarded(fn: Handler) -> Handler:
    """Wrap a tool handler so it returns an error block instead of raising."""

    @functools.wraps(fn)
    def wrapper(arguments: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return fn(arguments)
        except _EXPECTED as exc:
            return text_error(str(exc))
        except Exception as exc:  # noqa: BLE001 — a stdio MCP server must not die on one bad call
            log.exception("unexpected failure in %s", fn.__module__)
            return text_error(f"unexpected {type(exc).__name__} talking to the hardware: {exc}")

    return wrapper
