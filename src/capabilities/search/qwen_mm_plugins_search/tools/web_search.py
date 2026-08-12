"""MCP tool: text web search through the configured search backend."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class WebSearchArgs(BaseModel):
    queries: list[str] = Field(description="List of search queries to execute.")
    api_key: Optional[str] = Field(
        default=None,
        description="API key for the selected search backend (defaults to its backend-specific environment key).",
    )


TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the internet for text information. Returns search results with titles, snippets, and URLs."
    ),
    "args": WebSearchArgs,
}


def _format_results(docs: list[dict[str, Any]], start_id: int = 1) -> tuple[str, int]:
    """Render normalized docs; returns (text, next_id) with contiguous numbering."""
    lines = []
    i = start_id
    for doc in docs:
        url = doc.get("link", "")
        if not url:
            continue
        title = doc.get("title", "N/A")
        snippet = doc.get("snippet", "N/A")
        date = doc.get("date", "N/A")
        lines.append(f"[{i}] {url}\nTitle: {title}\nSnippet: {snippet}\nDate: {date}")
        i += 1
    text = "\n\n".join(lines) if lines else "No results found."
    return text, i


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from qwen_mm_plugins_search.backends import (
        backend_error,
        missing_key_error,
        resolve_api_key,
        resolve_backend,
        search_text,
    )
    from shared.content import require_dep, text_error

    queries = arguments.get("queries", [])
    if not queries:
        return text_error("queries list is empty")
    if err := require_dep("requests"):
        return err

    backend = resolve_backend()
    if error := backend_error(backend):
        return text_error(error)

    api_key = resolve_api_key(arguments, backend)
    if not api_key:
        return text_error(missing_key_error(backend))

    all_results = []
    idx = 1
    for q in queries:
        docs = search_text(q, backend, api_key)
        text, idx = _format_results(docs, idx)
        all_results.append(f"## Query: {q}\n{text}" if len(queries) > 1 else text)

    tip = "\n\n---\nTip: Use `web_extractor` on the most relevant URL above to read the full page for detailed confirmation."
    return [{"type": "text", "text": "\n\n".join(all_results) + tip}]
