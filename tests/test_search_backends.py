"""Offline contract tests for the Serper / Exa / Tavily / Serply / Keenable search adapters."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("mcp")

import shared.env as shared_env  # noqa: E402
from qwen_mm_plugins_search import backends, serper  # noqa: E402


def _fake_env(values):
    return lambda name, default=None: (
        (values.get(name) or "").strip() or ("auto" if name == "QWEN_MM_SEARCH_BACKEND" else default)
    )


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
        ({"EXA_API_KEY": "e", "SERPLY_API_KEY": "p"}, "exa"),
        ({"SERPLY_API_KEY": "p"}, "serply"),
        ({"SERPLY_API_KEY": "p", "KEENABLE_API_KEY": "k"}, "serply"),
        ({"KEENABLE_API_KEY": "k"}, "keenable"),
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
    [
        ("serper", "SERPER_API_KEY"),
        ("exa", "EXA_API_KEY"),
        ("tavily", "TAVILY_API_KEY"),
        ("serply", "SERPLY_API_KEY"),
        ("keenable", "KEENABLE_API_KEY"),
    ],
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
    assert error and "serper, exa, tavily, serply, keenable" in error
    assert backends.backend_error("exa") is None


def test_shared_config_catalog_lists_backend_selector_and_keys():
    fields = {key: (secret, default) for key, secret, _group, default, _description in shared_env.config_catalog()}
    assert fields["QWEN_MM_SEARCH_BACKEND"] == (False, "auto")
    assert fields["SERPER_API_KEY"] == (True, "")
    assert fields["EXA_API_KEY"] == (True, "")
    assert fields["TAVILY_API_KEY"] == (True, "")
    assert fields["SERPLY_API_KEY"] == (True, "")
    assert fields["KEENABLE_API_KEY"] == (True, "")


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


def test_serply_search_adapter_uses_get_and_normalizes_results(monkeypatch):
    calls = []

    def fake_get(url, params, headers, **kwargs):
        calls.append((url, params, headers, kwargs))
        return {"results": [{"link": "https://p", "title": "P", "description": "serply body"}]}

    monkeypatch.setattr(backends, "_get_json", fake_get)
    docs = backends.search_text("q", "serply", "key")

    assert docs == [{"link": "https://p", "title": "P", "snippet": "serply body", "date": "N/A"}]
    url, params, headers, kwargs = calls[0]
    assert url == f"{backends.SERPLY_BASE}/v1/search/"
    assert params["q"] == "q" and params["num"] == 10
    assert headers["X-Api-Key"] == "key"
    assert kwargs["max_retries"] == 10


def test_serply_search_adapter_handles_failed_request(monkeypatch):
    monkeypatch.setattr(backends, "_get_json", lambda *_a, **_k: None)
    assert backends.search_text("q", "serply", "key") == []


def test_serply_extraction_adapter_returns_markdown_text(monkeypatch):
    calls = []

    def fake_post(url, payload, headers, **kwargs):
        calls.append((url, payload, headers, kwargs))
        return "# Page\n\nbody\n"

    monkeypatch.setattr(backends, "_post_text", fake_post)
    assert backends.extract_page("https://page", "serply", "key") == "# Page\n\nbody"

    url, payload, headers, kwargs = calls[0]
    assert url == f"{backends.SERPLY_BASE}/v1/request"
    assert payload == {"url": "https://page", "method": "GET", "response_type": "markdown"}
    assert headers["X-Api-Key"] == "key"
    assert kwargs["max_retries"] == 3


@pytest.mark.parametrize(
    "response,expected",
    [(None, "Error scraping https://page"), ("", "No content extracted."), ("  \n", "No content extracted.")],
)
def test_serply_extraction_failure_and_empty_body_are_reported(monkeypatch, response, expected):
    monkeypatch.setattr(backends, "_post_text", lambda *_a, **_k: response)
    assert backends.extract_page("https://page", "serply", "key") == expected


@pytest.mark.parametrize(
    "api_key,path,key_header",
    [("key", "/v1/search", {"X-API-Key": "key"}), ("", "/v1/search/public", {})],
)
def test_keenable_search_adapter_routes_by_key_and_normalizes_results(monkeypatch, api_key, path, key_header):
    calls = []
    long_text = "k" * (backends.SEARCH_SNIPPET_LIMIT + 50)

    def fake_post(url, payload, headers, **kwargs):
        calls.append((url, payload, headers, kwargs))
        return {
            "results": [
                {"url": "https://k", "title": "K", "description": "", "snippet": long_text, "acquired_at": "2026"},
                {"url": "https://k2", "title": "", "description": "", "snippet": ""},
            ]
        }

    monkeypatch.setattr(backends, "_post_json", fake_post)
    docs = backends.search_text("q", "keenable", api_key)

    assert docs == [
        {"link": "https://k", "title": "K", "snippet": "k" * backends.SEARCH_SNIPPET_LIMIT, "date": "N/A"},
        {"link": "https://k2", "title": "N/A", "snippet": "N/A", "date": "N/A"},
    ]
    url, payload, headers, kwargs = calls[0]
    assert url == f"{backends.KEENABLE_BASE}{path}"
    assert payload == {"query": "q", "max_results": 10}
    assert headers == {"X-Keenable-Title": "Qwen-MM-Plugins", **key_header}
    assert kwargs["max_retries"] == 10


def test_keenable_search_adapter_handles_failed_request(monkeypatch):
    monkeypatch.setattr(backends, "_post_json", lambda *_a, **_k: None)
    assert backends.search_text("q", "keenable", "") == []


@pytest.mark.parametrize(
    "api_key,path,key_header",
    [("key", "/v1/fetch", {"X-API-Key": "key"}), ("", "/v1/fetch/public", {})],
)
def test_keenable_extraction_adapter_routes_by_key_and_returns_markdown(monkeypatch, api_key, path, key_header):
    calls = []

    def fake_get(url, params, headers, **kwargs):
        calls.append((url, params, headers, kwargs))
        return {"url": "https://page", "title": "Page", "content": "# Page\n\nbody", "description": ""}

    monkeypatch.setattr(backends, "_get_json", fake_get)
    assert backends.extract_page("https://page", "keenable", api_key) == "# Page\n\nbody"

    url, params, headers, kwargs = calls[0]
    assert url == f"{backends.KEENABLE_BASE}{path}"
    assert params == {"url": "https://page"}
    assert headers == {"X-Keenable-Title": "Qwen-MM-Plugins", **key_header}
    assert kwargs["max_retries"] == 3


@pytest.mark.parametrize(
    "response,expected",
    [(None, "Error scraping https://page"), ({"content": ""}, "No content extracted.")],
)
def test_keenable_extraction_failure_and_empty_body_are_reported(monkeypatch, response, expected):
    monkeypatch.setattr(backends, "_get_json", lambda *_a, **_k: response)
    assert backends.extract_page("https://page", "keenable", "") == expected


def test_selected_keenable_runs_keyless_through_the_tools(monkeypatch):
    from qwen_mm_plugins_search.tools import web_extractor, web_search

    monkeypatch.setattr(shared_env, "get_env", _fake_env({"QWEN_MM_SEARCH_BACKEND": "keenable"}))
    calls = []

    def fake_post(url, payload, headers, **kwargs):
        calls.append((url, headers))
        return {"results": [{"url": "https://k", "title": "K", "snippet": "body"}]}

    def fake_get(url, params, headers, **kwargs):
        calls.append((url, headers))
        return {"content": "# Page"}

    monkeypatch.setattr(backends, "_post_json", fake_post)
    monkeypatch.setattr(backends, "_get_json", fake_get)

    assert "[1] https://k" in web_search.handle({"queries": ["q"]})[0]["text"]
    assert "# Page" in web_extractor.handle({"urls": ["https://page"], "goal": ""})[0]["text"]
    assert [url for url, _headers in calls] == [
        f"{backends.KEENABLE_BASE}/v1/search/public",
        f"{backends.KEENABLE_BASE}/v1/fetch/public",
    ]
    assert all("X-API-Key" not in headers for _url, headers in calls)


def test_keyed_backends_still_report_a_missing_key(monkeypatch):
    from qwen_mm_plugins_search.tools import web_search

    monkeypatch.setattr(shared_env, "get_env", _fake_env({"QWEN_MM_SEARCH_BACKEND": "serply"}))
    monkeypatch.setattr(backends, "_get_json", lambda *_a, **_k: pytest.fail("no request without a key"))
    assert "no API key for serply" in web_search.handle({"queries": ["q"]})[0]["text"]
