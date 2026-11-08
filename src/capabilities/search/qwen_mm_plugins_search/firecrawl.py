"""Client for the public Firecrawl (api.firecrawl.dev) v2 API.

One home for the base URL, bearer auth header, and POST-with-retry policy used by the
firecrawl web_search / web_extractor backend. Lives in the search capability (package-local)
rather than src/shared/ because Firecrawl is used only here; it mirrors the shape of
serper.py (a base-URL constant + a retry helper).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

FIRECRAWL_BASE = "https://api.firecrawl.dev/v2"  # API reference: https://docs.firecrawl.dev
DEFAULT_MAX_RETRIES = 5
DEFAULT_TIMEOUT = 60
_BACKOFF_CAP_SECONDS = 10


def post_firecrawl(
    path: str,
    payload: dict[str, Any],
    api_key: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """POST to a Firecrawl endpoint (e.g. "search", "scrape") → parsed JSON.

    Retries transient failures with capped exponential backoff; returns None once
    retries are exhausted, leaving it to the caller to decide how to surface that.
    """
    import requests

    from shared.retry import retry_call

    url = f"{FIRECRAWL_BASE}/{path}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _post() -> dict[str, Any]:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _retryable(error: Exception) -> bool:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        return not isinstance(status, int) or status in (408, 429) or status >= 500

    try:
        return retry_call(
            _post,
            attempts=max_retries,
            mode="exp",
            cap=_BACKOFF_CAP_SECONDS,
            should_retry=_retryable,
            on_exhausted="none",
            log=log,
        )
    except requests.HTTPError:
        return None
