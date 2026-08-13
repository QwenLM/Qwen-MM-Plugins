"""Unit tests for the shared network clients (api_openai / api_dashscope).

These are the most branch-dense, refactor-fragile modules in the repo and had zero
coverage (the suite is otherwise real-or-synthetic with no mocking). They exploit the
lazy `import openai`/`import requests` inside each function: monkeypatching the real
module's attribute is enough, no live network.
"""

import httpx
import pytest

import shared.api_dashscope as dsc
import shared.api_openai as oa
import shared.retry as sr

# ── api_dashscope.retry_call ─────────────────────────────────────────


def test_retry_call_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert dsc.retry_call(fn) == "ok"
    assert calls["n"] == 3


def test_retry_call_reraises_after_max(monkeypatch):
    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RuntimeError("always")

    with pytest.raises(RuntimeError, match="always"):
        dsc.retry_call(fn)
    assert calls["n"] == dsc._SERVICE_MAX_RETRIES


# ── api_dashscope.poll_dashscope_task ────────────────────────────────


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass  # a real 200 Response.raise_for_status() is a no-op

    def json(self):
        return self._p


def test_poll_returns_on_terminal_status(monkeypatch):
    import requests

    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    seq = iter(
        [
            {"output": {"task_status": "PENDING"}},
            {"output": {"task_status": "RUNNING"}},
            {"output": {"task_status": "SUCCEEDED", "results": [1]}},
        ]
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(next(seq)))
    out = dsc.poll_dashscope_task("tid", "key", base_url=dsc.API_V1, interval=0, timeout=100)
    assert out["output"]["task_status"] == "SUCCEEDED"


def test_poll_returns_synthetic_timeout():
    # timeout=0 → the while-loop body never runs → synthetic TIMEOUT dict, no HTTP call.
    out = dsc.poll_dashscope_task("tid", "key", base_url=dsc.API_V1, interval=0, timeout=0)
    assert out["output"]["task_status"] == "TIMEOUT"
    assert out["output"]["task_id"] == "tid"


# ── api_openai.call_openai_chat ──────────────────────────────────────


def test_call_openai_chat_missing_key_guard():
    with pytest.raises(RuntimeError, match="no API key"):
        oa.call_openai_chat(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="EMPTY",
            model="m",
            messages=[],
        )


class _FakeCompletions:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = 0
        self.seen: list[dict] = []  # the kwargs of each attempt, for the hint-dropping tests

    def create(self, **kwargs):
        self.calls += 1
        self.seen.append(kwargs)
        return self._behavior(self.calls)


def _install_fake_openai(monkeypatch, behavior):
    import openai

    holder = {}

    def factory(**kwargs):
        client = type("_Client", (), {})()
        client.chat = type("_Chat", (), {})()
        client.chat.completions = _FakeCompletions(behavior)
        holder["client"] = client
        return client

    monkeypatch.setattr(openai, "OpenAI", factory)
    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    return holder


def test_call_openai_chat_retries_transient_then_succeeds(monkeypatch):
    import openai

    req = httpx.Request("POST", "http://local/v1")

    def behavior(n):
        if n < 3:
            raise openai.APITimeoutError(request=req)
        return "RESULT"

    holder = _install_fake_openai(monkeypatch, behavior)
    out = oa.call_openai_chat(base_url="http://local", api_key="k", max_retries=3, model="m", messages=[])
    assert out == "RESULT"
    assert holder["client"].chat.completions.calls == 3


def test_call_openai_chat_non_transient_raises_without_retry(monkeypatch):
    def behavior(n):
        raise ValueError("bad request")

    holder = _install_fake_openai(monkeypatch, behavior)
    with pytest.raises(ValueError, match="bad request"):
        oa.call_openai_chat(base_url="http://local", api_key="k", max_retries=3, model="m", messages=[])
    assert holder["client"].chat.completions.calls == 1  # not retried


def _bad_request(message="Extra inputs are not permitted"):
    import openai

    req = httpx.Request("POST", "http://local/v1")
    return openai.BadRequestError(message, response=httpx.Response(400, request=req), body=None)


def test_call_openai_chat_drops_optional_extra_body_on_400(monkeypatch):
    """A strict endpoint 400s on the hint; the call is retried without it and succeeds."""

    def behavior(n):
        if n == 1:
            raise _bad_request()
        return "RESULT"

    holder = _install_fake_openai(monkeypatch, behavior)
    out = oa.call_openai_chat(
        base_url="http://local",
        api_key="k",
        max_retries=3,
        model="m",
        messages=[],
        optional_extra_body={"enable_thinking": False},
    )
    seen = holder["client"].chat.completions.seen
    assert out == "RESULT"
    assert len(seen) == 2  # the 400 is not itself retried, only the hint-free call follows
    assert seen[0]["extra_body"] == {"enable_thinking": False}
    assert "extra_body" not in seen[1]


def test_call_openai_chat_keeps_required_extra_body_when_dropping_hints(monkeypatch):
    """Only the optional fields are dropped — an explicit extra_body survives the retry."""

    def behavior(n):
        if n == 1:
            raise _bad_request()
        return "RESULT"

    holder = _install_fake_openai(monkeypatch, behavior)
    out = oa.call_openai_chat(
        base_url="http://local",
        api_key="k",
        max_retries=3,
        model="m",
        messages=[],
        extra_body={"modalities": ["text"]},
        optional_extra_body={"enable_thinking": False},
    )
    seen = holder["client"].chat.completions.seen
    assert out == "RESULT"
    assert seen[0]["extra_body"] == {"modalities": ["text"], "enable_thinking": False}
    assert seen[1]["extra_body"] == {"modalities": ["text"]}


def test_call_openai_chat_400_without_hints_is_not_retried(monkeypatch):
    """A 400 with nothing droppable is the caller's error and propagates on the first attempt."""
    import openai

    def behavior(n):
        raise _bad_request("image is not a valid input")

    holder = _install_fake_openai(monkeypatch, behavior)
    with pytest.raises(openai.BadRequestError, match="image is not a valid input"):
        oa.call_openai_chat(base_url="http://local", api_key="k", max_retries=3, model="m", messages=[])
    assert holder["client"].chat.completions.calls == 1


def test_call_openai_chat_400_after_dropping_hints_propagates(monkeypatch):
    """When the hint was not the cause, the second 400 surfaces rather than the first."""
    import openai

    def behavior(n):
        raise _bad_request("model does not exist" if n > 1 else "Extra inputs are not permitted")

    holder = _install_fake_openai(monkeypatch, behavior)
    with pytest.raises(openai.BadRequestError, match="model does not exist"):
        oa.call_openai_chat(
            base_url="http://local",
            api_key="k",
            max_retries=3,
            model="m",
            messages=[],
            optional_extra_body={"enable_thinking": False},
        )
    assert holder["client"].chat.completions.calls == 2


# ── shared.retry.retry_call (the primitive itself) ───────────────────


def test_retry_returns_on_success():
    assert sr.retry_call(lambda: 42, attempts=3) == 42


def test_retry_on_exhausted_none_returns_none(monkeypatch):
    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RuntimeError("always")

    assert sr.retry_call(fn, attempts=3, mode="exp", cap=10, on_exhausted="none") is None
    assert calls["n"] == 3


def test_retry_predicate_propagates_non_matching(monkeypatch):
    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        sr.retry_call(fn, attempts=5, should_retry=lambda e: isinstance(e, KeyError))
    assert calls["n"] == 1  # predicate rejects → propagates on first try, no retry


# ── B1: poll + download survive a transient blip on a billed job ─────


def test_poll_retries_transient_get(monkeypatch):
    import requests

    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("blip")
        return _Resp({"output": {"task_status": "SUCCEEDED"}})

    monkeypatch.setattr(requests, "get", fake_get)
    out = dsc.poll_dashscope_task("t", "k", base_url=dsc.API_V1, interval=0, timeout=100)
    assert out["output"]["task_status"] == "SUCCEEDED"
    assert calls["n"] == 2  # the transient GET was retried, not aborted


def test_save_url_to_dir_retries_transient(monkeypatch, tmp_path):
    import requests

    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    class _Content:
        content = b"DATA"

        def raise_for_status(self):
            pass  # a real 200 Response.raise_for_status() is a no-op

    def fake_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("blip")
        return _Content()

    monkeypatch.setattr(requests, "get", fake_get)
    dest = tmp_path / "out.bin"
    dsc.save_url_to_dir("http://x/a.bin", str(dest))
    assert dest.read_bytes() == b"DATA"
    assert calls["n"] == 2
