"""Pluggable text-search and page-extraction backends.

The public MCP tool schemas stay provider-neutral. ``QWEN_MM_SEARCH_BACKEND``
can pin the transport at call time; otherwise configured keys are discovered in
the order Serper, Tavily, Exa. This module normalizes provider responses into
the legacy Serper-shaped fields consumed by the tool formatters.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

SUPPORTED_BACKENDS = ("serper", "exa", "tavily")
AUTO_BACKEND_PRIORITY = ("serper", "tavily", "exa")
BACKEND_KEY_ENVS = {
    "serper": "SERPER_API_KEY",
    "exa": "EXA_API_KEY",
    "tavily": "TAVILY_API_KEY",
}

EXA_BASE = "https://api.exa.ai"
TAVILY_BASE = "https://api.tavily.com"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 5
SEARCH_SNIPPET_LIMIT = 1000
EXTRACT_CONTENT_LIMIT = 8000
_BACKOFF_CAP_SECONDS = 10


def resolve_backend() -> str:
    """Return an explicit backend, or auto-select the first configured key."""
    from shared.env import get_env

    selected = (get_env("QWEN_MM_SEARCH_BACKEND") or "").strip().lower()
    if selected and selected != "auto":
        return selected

    for backend in AUTO_BACKEND_PRIORITY:
        if get_env(BACKEND_KEY_ENVS[backend]):
            return backend

    # Preserve the long-standing missing-key error when no provider is configured.
    return "serper"


def backend_error(backend: str) -> str | None:
    """Return a user-facing error for an unsupported backend, otherwise ``None``."""
    if backend in SUPPORTED_BACKENDS:
        return None
    choices = ", ".join(SUPPORTED_BACKENDS)
    return f"unsupported search backend {backend!r}. Set QWEN_MM_SEARCH_BACKEND to one of: {choices}."


def resolve_api_key(arguments: dict[str, Any], backend: str) -> str:
    """Explicit ``api_key`` argument, then the selected backend's environment key."""
    from shared.env import get_env

    explicit = arguments.get("api_key")
    env_name = BACKEND_KEY_ENVS.get(backend)
    return explicit or (get_env(env_name) if env_name else None) or ""


def missing_key_error(backend: str) -> str:
    """Return the missing-key message for a validated backend name."""
    env_name = BACKEND_KEY_ENVS[backend]
    return f"no API key for {backend}. Set {env_name} or pass api_key."


def _post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """POST JSON with the same transient retry policy used by the Serper client."""
    import requests

    from shared.retry import retry_call

    request_headers = {"Content-Type": "application/json", **headers}

    def _post() -> dict[str, Any]:
        response = requests.post(url, json=payload, headers=request_headers, timeout=timeout)
        response.raise_for_status()
        return response.json()

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


def _normalized_result(
    doc: dict[str, Any],
    *,
    url_key: str,
    snippet_key: str,
    date_key: str,
) -> dict[str, Any]:
    """Map one provider result onto the legacy formatter's common fields."""
    return {
        "link": doc.get(url_key, ""),
        "title": doc.get("title") or "N/A",
        "snippet": doc.get(snippet_key) or "N/A",
        "date": doc.get(date_key) or "N/A",
    }


def search_text(query: str, backend: str, api_key: str) -> list[dict[str, Any]]:
    """Search one query and return normalized ``link/title/snippet/date`` results."""
    if backend == "serper":
        from qwen_mm_plugins_search.serper import post_serper

        data = post_serper(
            "search",
            {"q": query, "gl": "us", "hl": "en", "location": "United States", "num": 10},
            api_key,
            max_retries=10,
        )
        return [
            _normalized_result(doc, url_key="link", snippet_key="snippet", date_key="date")
            for doc in (data or {}).get("organic", [])
        ]

    if backend == "exa":
        data = _post_json(
            f"{EXA_BASE}/search",
            {
                "query": query,
                "numResults": 10,
                "contents": {"text": {"maxCharacters": SEARCH_SNIPPET_LIMIT}},
            },
            {"x-api-key": api_key},
            max_retries=10,
        )
        return [
            _normalized_result(doc, url_key="url", snippet_key="text", date_key="publishedDate")
            for doc in (data or {}).get("results", [])
        ]

    if backend == "tavily":
        data = _post_json(
            f"{TAVILY_BASE}/search",
            {"query": query, "search_depth": "basic", "max_results": 10, "include_answer": False},
            {"Authorization": f"Bearer {api_key}"},
            max_retries=10,
        )
        return [
            _normalized_result(doc, url_key="url", snippet_key="content", date_key="published_date")
            for doc in (data or {}).get("results", [])
        ]

    raise ValueError(backend_error(backend))


def extract_page(url: str, backend: str, api_key: str) -> str:
    """Extract one page as markdown/text, or return an error/empty-content note."""
    if backend == "serper":
        from qwen_mm_plugins_search.serper import post_serper

        data = post_serper("scrape", {"url": url, "includeMarkdown": True}, api_key, max_retries=3)
        if data is None:
            return f"Error scraping {url}"
        return data.get("markdown") or data.get("text") or "No content extracted."

    if backend == "exa":
        data = _post_json(
            f"{EXA_BASE}/contents",
            {"urls": [url], "text": {"maxCharacters": EXTRACT_CONTENT_LIMIT}},
            {"x-api-key": api_key},
            max_retries=3,
        )
        statuses = (data or {}).get("statuses", [])
        if statuses and statuses[0].get("status") != "success":
            return f"Error scraping {url}"
        results = (data or {}).get("results", [])
        if not results:
            return f"Error scraping {url}"
        return results[0].get("text") or "No content extracted."

    if backend == "tavily":
        data = _post_json(
            f"{TAVILY_BASE}/extract",
            {"urls": [url], "format": "markdown"},
            {"Authorization": f"Bearer {api_key}"},
            max_retries=3,
        )
        results = (data or {}).get("results", [])
        if not results:
            return f"Error scraping {url}"
        return results[0].get("raw_content") or "No content extracted."

    raise ValueError(backend_error(backend))
