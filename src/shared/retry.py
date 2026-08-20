"""One retry/backoff policy for the network clients.

api_openai (chat), api_dashscope (async services) and core's serper client all need the
same attempt/backoff loop; before this they hand-rolled four slightly different copies.
This centralizes the *mechanism* while leaving each caller's policy (attempt count, backoff
shape, which errors are retryable, and whether to raise or return None on exhaustion) as
call-site arguments — different endpoints legitimately warrant different policies.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

_log = logging.getLogger(__name__)
T = TypeVar("T")


def _wait_seconds(attempt: int, base: float, mode: str, cap: float | None, jitter: float = 0.0) -> float:
    wait = base * attempt if mode == "linear" else base * (2 ** (attempt - 1))
    if jitter:
        wait += random.uniform(0.0, jitter)
    if cap is not None:  # cap bounds the final sleep, jitter included
        wait = min(wait, cap)
    return wait


def _exception_label(error: Exception) -> str:
    """Return a log-safe error label without provider-controlled response or request text."""
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(error, "response", None), "status_code", None)
    suffix = f" (HTTP {status})" if isinstance(status, int) else ""
    return f"{type(error).__name__}{suffix}"


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int,
    base_backoff: float = 1.0,
    mode: str = "linear",  # "linear" → base*attempt; "exp" → base*2**(attempt-1)
    cap: float | None = None,  # ceiling (seconds) on the inter-attempt sleep
    jitter: float = 0.0,  # add uniform(0, jitter) seconds on top of each sleep (de-syncs retry herds)
    should_retry: Callable[[Exception], bool] | None = None,
    on_exhausted: str = "raise",  # "raise" the last error, or "none" → return None
    log: logging.Logger = _log,
) -> T | None:
    """Call ``fn()`` with retry.

    Retries while ``should_retry(exc)`` (default: any exception); an exception the predicate
    rejects propagates immediately. After ``attempts`` tries either re-raises the last
    exception (``on_exhausted="raise"``) or returns ``None`` (``on_exhausted="none"``).
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — the predicate decides what is retryable
            if should_retry is not None and not should_retry(e):
                raise
            last = e
            if attempt == attempts:
                break
            wait = _wait_seconds(attempt, base_backoff, mode, cap, jitter)
            # Provider-controlled error messages can echo request bodies, including base64 media
            # or credentials. Log only a local type and numeric HTTP status.
            log.warning(
                "call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt,
                attempts,
                _exception_label(e),
                wait,
            )
            time.sleep(wait)
    if on_exhausted == "raise":
        if last is None:  # attempts < 1 → the loop never ran; explicit (assert vanishes under -O)
            raise RuntimeError(f"retry_call made no attempt (attempts={attempts})")
        raise last
    return None
