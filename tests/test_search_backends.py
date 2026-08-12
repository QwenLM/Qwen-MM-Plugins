"""Offline contract tests for the Serper / Exa / Tavily search adapters."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("mcp")

import shared.env as shared_env  # noqa: E402
from qwen_mm_plugins_search import backends, serper  # noqa: E402


def _fake_env(values):
    return lambda name, default=None: values.get(name, default)


def test_explicit_backend_is_normalized_and_does_not_fallback(monkeypatch):
    monkeypatch.setattr(
        shared_env,
        "get_env",
        _fake_env({"QWEN_MM_SEARCH_BACKEND": "  TAVILY  ", "SERPER_API_KEY": "serper-key"}),
    )
    assert backends.resolve_backend() == "tavily"

    monkeypatch.setattr(
        shared_env,
        "get_env",
        _fake_env({"QWEN_MM_SEARCH_BACKEND": "serper", "TAVILY_API_KEY": "tavily-key"}),
    )
    assert backends.resolve_backend() == "serper"


@pytest.mark.parametrize(
    "values,expected",
    [
        (
            {"SERPER_API_KEY": "s", "TAVILY_API_KEY": "t", "EXA_API_KEY": "e"},
            "serper",
        ),
        ({"TAVILY_API_KEY": "t", "EXA_API_KEY": "e"}, "tavily"),
        ({"EXA_API_KEY": "e"}, "exa"),
        ({}, "serper"),
    ],
)
def test_automatic_backend_priority(monkeypatch, values, expected):
    monkeypatch.setattr(shared_env, "get_env", _fake_env(values))
    assert backends.resolve_backend() == expected


@pytest.mark.parametrize("selector", ["", "   ", "auto", " AUTO "])
def test_blank_or_auto_selector_uses_configured_keys(monkeypatch, selector):
    monkeypatch.setattr(
        shared_env,
        "get_env",
        _fake_env({"QWEN_MM_SEARCH_BACKEND": selector, "TAVILY_API_KEY": "tavily-key"}),
    )
    assert backends.resolve_backend() == "tavily"


@pytest.mark.parametrize(
    "backend,env_name",
    [("serper", "SERPER_API_KEY"), ("exa", "EXA_API_KEY"), ("tavily", "TAVILY_API_KEY")],
)
def test_backend_specific_key_resolution_and_explicit_override(monkeypatch, backend, env_name):
    seen = []

    def fake_get_env(name, default=None):
        seen.append(name)
        return "env-key"

    monkeypatch.setattr(shared_env, "get_env", fake_get_env)
    assert backends.resolve_api_key({}, backend) == "env-key"
    assert seen == [env_name]

    seen.clear()
    assert backends.resolve_api_key({"api_key": "explicit"}, backend) == "explicit"
    assert seen == []


def test_invalid_backend_message_lists_choices():
    error = backends.backend_error("other")
    assert error and "serper, exa, tavily" in error
    assert backends.backend_error("exa") is None


def test_shared_config_catalog_lists_backend_selector_and_keys():
    fields = {key: (secret, default) for key, secret, _group, default, _description in shared_env.CONFIG_FIELDS}
    assert fields["QWEN_MM_SEARCH_BACKEND"] == (False, "auto")
    assert fields["SERPER_API_KEY"] == (True, "")
    assert fields["EXA_API_KEY"] == (True, "")
    assert fields["TAVILY_API_KEY"] == (True, "")


def test_serper_search_adapter_normalizes_results(monkeypatch):
    calls = []

    def fake_post(path, payload, api_key, **kwargs):
        calls.append((path, payload, api_key, kwargs))
        return {"organic": [{"link": "https://s", "title": "S", "snippet": "body", "date": "today"}]}

    monkeypatch.setattr(serper, "post_serper", fake_post)
    docs = backends.search_text("q", "serper", "key")

    assert docs == [{"link": "https://s", "title": "S", "snippet": "body", "date": "today"}]
    assert calls[0][0] == "search"
    assert calls[0][1]["q"] == "q" and calls[0][1]["num"] == 10


@pytest.mark.parametrize(
    "backend,response,expected,url_suffix,expected_header,payload_checks",
    [
        (
            "exa",
            {"results": [{"url": "https://e", "title": "E", "text": "exa body", "publishedDate": "2026"}]},
            {"link": "https://e", "title": "E", "snippet": "exa body", "date": "2026"},
            "/search",
            ("x-api-key", "key"),
            {
                "query": "q",
                "numResults": 10,
                "contents": {"text": {"maxCharacters": backends.SEARCH_SNIPPET_LIMIT}},
            },
        ),
        (
            "tavily",
            {"results": [{"url": "https://t", "title": "T", "content": "tavily body"}]},
            {"link": "https://t", "title": "T", "snippet": "tavily body", "date": "N/A"},
            "/search",
            ("Authorization", "Bearer key"),
            {"query": "q", "search_depth": "basic", "max_results": 10, "include_answer": False},
        ),
    ],
)
def test_non_serper_search_adapters(
    monkeypatch,
    backend,
    response,
    expected,
    url_suffix,
    expected_header,
    payload_checks,
):
    calls: list[tuple[str, dict[str, Any], dict[str, str], dict[str, Any]]] = []

    def fake_post(url, payload, headers, **kwargs):
        calls.append((url, payload, headers, kwargs))
        return response

    monkeypatch.setattr(backends, "_post_json", fake_post)
    docs = backends.search_text("q", backend, "key")

    assert docs == [expected]
    url, payload, headers, kwargs = calls[0]
    assert url.endswith(url_suffix)
    assert headers[expected_header[0]] == expected_header[1]
    assert payload == payload_checks
    assert kwargs["max_retries"] == 10


@pytest.mark.parametrize(
    "backend,response,expected,url_suffix,expected_header,expected_payload",
    [
        (
            "exa",
            {"results": [{"url": "https://e", "text": "exa page"}]},
            "exa page",
            "/contents",
            ("x-api-key", "key"),
            {"urls": ["https://page"], "text": {"maxCharacters": backends.EXTRACT_CONTENT_LIMIT}},
        ),
        (
            "tavily",
            {"results": [{"url": "https://page", "raw_content": "tavily page"}]},
            "tavily page",
            "/extract",
            ("Authorization", "Bearer key"),
            {"urls": ["https://page"], "format": "markdown"},
        ),
    ],
)
def test_non_serper_extraction_adapters(
    monkeypatch,
    backend,
    response,
    expected,
    url_suffix,
    expected_header,
    expected_payload,
):
    calls = []

    def fake_post(url, payload, headers, **kwargs):
        calls.append((url, payload, headers, kwargs))
        return response

    monkeypatch.setattr(backends, "_post_json", fake_post)
    assert backends.extract_page("https://page", backend, "key") == expected

    url, payload, headers, kwargs = calls[0]
    assert url.endswith(url_suffix)
    assert headers[expected_header[0]] == expected_header[1]
    assert payload == expected_payload
    assert kwargs["max_retries"] == 3


@pytest.mark.parametrize("backend", ["exa", "tavily"])
def test_extraction_empty_result_is_reported(monkeypatch, backend):
    monkeypatch.setattr(backends, "_post_json", lambda *_a, **_k: {"results": []})
    assert backends.extract_page("https://page", backend, "key") == "Error scraping https://page"


def test_exa_extraction_checks_per_url_status(monkeypatch):
    monkeypatch.setattr(
        backends,
        "_post_json",
        lambda *_a, **_k: {
            "results": [{"text": "stale"}],
            "statuses": [{"id": "https://page", "status": "error"}],
        },
    )
    assert backends.extract_page("https://page", "exa", "key") == "Error scraping https://page"
