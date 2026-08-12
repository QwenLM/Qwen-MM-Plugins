"""MCP tool: web page extraction through the configured search backend."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

CONTENT_LIMIT = 8000  # chars of scraped page kept per URL


class WebExtractorArgs(BaseModel):
    urls: list[str] = Field(description="URLs to crawl and extract content from.", min_length=1)
    goal: str = Field(description="What information to extract or focus on.")
    api_key: Optional[str] = Field(
        default=None,
        description="API key for the selected search backend (defaults to its backend-specific environment key).",
    )


TOOL: dict[str, Any] = {
    "name": "web_extractor",
    "description": (
        "Crawl and extract content from web pages, with optional summarization. "
        "Returns the extracted text or a summary focused on the specified goal."
    ),
    "args": WebExtractorArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from qwen_mm_plugins_search.backends import (
        backend_error,
        extract_page,
        missing_key_error,
        resolve_api_key,
        resolve_backend,
    )
    from shared.content import require_dep, text_error

    urls = arguments.get("urls", [])
    goal = arguments.get("goal", "")
    if not urls:
        return text_error("urls list is empty")
    if err := require_dep("requests"):
        return err

    backend = resolve_backend()
    if error := backend_error(backend):
        return text_error(error)

    api_key = resolve_api_key(arguments, backend)
    if not api_key:
        return text_error(missing_key_error(backend))

    results = []
    for url in urls:
        content = extract_page(url, backend, api_key)
        if content and not content.startswith("Error"):
            truncated = content[:CONTENT_LIMIT]
            results.append(f"## {url}\n(Goal: {goal})\n\n{truncated}" if goal else f"## {url}\n\n{truncated}")
        else:
            results.append(f"## {url}\n{content}")

    text = "\n\n".join(results) if results else "Could not extract content."
    return [{"type": "text", "text": text}]
