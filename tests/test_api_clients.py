"""Unit tests for the shared network clients (api_openai / api_dashscope).

These are the most branch-dense, refactor-fragile modules in the repo and had zero
coverage (the suite is otherwise real-or-synthetic with no mocking). They exploit the
lazy `import openai`/`import requests` inside each function: monkeypatching the real
module's attribute is enough, no live network.
"""

import base64
import io
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import shared.api_dashscope as dsc
import shared.api_omni as omni
import shared.api_openai as oa
import shared.retry as sr


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_encode_prepared_image_preserves_dimensions_and_alpha(mode):
    from PIL import Image

    image = Image.new(mode, (37, 29), (0, 0, 0, 84) if mode == "RGBA" else (0, 0, 0))
    part = oa.encode_image_source(image)
    data = part["image_url"]["url"].split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(data))) as encoded:
        assert encoded.size == image.size
        assert encoded.getpixel((0, 0)) == image.getpixel((0, 0))


def test_encode_image_source_preserves_file_bytes_and_remote_urls(sample_image):
    part = oa.encode_image_source(sample_image)
    assert base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) == Path(sample_image).read_bytes()
    remote = "https://example.com/photo.jpg?signature=abc#view"
    assert oa.encode_image_source(remote)["image_url"]["url"] == remote


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


def test_model_resolvers_use_explicit_env_then_builtin(monkeypatch):
    values = {}
    monkeypatch.setattr(oa, "get_env", values.get)
    monkeypatch.setattr(omni, "get_env", values.get)
    assert oa.resolve_vl_model() == oa.DEFAULT_MODEL
    assert omni.resolve_omni_model() == omni.DEFAULT_OMNI_MODEL

    values["QWEN_MM_API_VL_MODEL"] = "env-vl"
    assert oa.resolve_vl_model() == "env-vl"
    assert omni.resolve_omni_model() == omni.DEFAULT_OMNI_MODEL

    values["QWEN_MM_API_OMNI_MODEL"] = "env-omni"
    assert oa.resolve_vl_model() == "env-vl"
    assert omni.resolve_omni_model() == "env-omni"
    assert oa.resolve_vl_model("explicit-vl") == "explicit-vl"
    assert omni.resolve_omni_model("explicit-omni") == "explicit-omni"


@pytest.mark.parametrize(
    "base_url,missing_key,explicit_key,expected_key",
    [
        (None, None, None, "dashscope"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", None, None, "dashscope"),
        ("https://api.orcarouter.ai/v1", None, None, "orca"),
        ("https://api.orcarouter.ai/v1", "ORCAROUTER_API_KEY", None, "EMPTY"),
        ("https://api.orcarouter.ai/v1", None, "explicit", "explicit"),
        ("https://api.orcarouter.ai.example/v1", None, None, "EMPTY"),
        ("https://openrouter.ai/api/v1", None, None, "openrouter"),
        ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None, "EMPTY"),
        ("https://openrouter.ai/api/v1", None, "explicit", "explicit"),
        ("https://openrouter.ai.example/api/v1", None, None, "EMPTY"),
        ("http://localhost:8000/v1", None, None, "EMPTY"),
        ("http://localhost:8000/v1", None, "custom", "custom"),
    ],
)
def test_endpoint_selects_key_by_host(monkeypatch, base_url, missing_key, explicit_key, expected_key):
    values = {
        "DASHSCOPE_API_KEY": "dashscope",
        "ORCAROUTER_API_KEY": "orca",
        "OPENROUTER_API_KEY": "openrouter",
    }
    if missing_key:
        del values[missing_key]
    monkeypatch.setattr(oa, "get_env", values.get)
    arguments = {"base_url": base_url, "api_key": explicit_key}
    expected = (base_url or oa.DEFAULT_DASHSCOPE_BASE_URL, expected_key)
    assert oa.resolve_openai_endpoint(arguments) == expected
    assert omni.resolve_omni_endpoint(arguments) == expected


def test_openrouter_endpoint_from_config(monkeypatch, tmp_path):
    import shared.env as env

    config = tmp_path / "config"
    config.write_text(
        "DASHSCOPE_BASE_URL=https://openrouter.ai/api/v1\n"
        "OPENROUTER_API_KEY=config-openrouter\n"
        "DASHSCOPE_API_KEY=config-dashscope\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    for key in ("DASHSCOPE_BASE_URL", "OPENROUTER_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(env, "_config_cache", None)

    for resolve in (oa.resolve_openai_endpoint, omni.resolve_omni_endpoint):
        assert resolve({}) == ("https://openrouter.ai/api/v1", "config-openrouter")
        assert resolve({"base_url": oa.DEFAULT_DASHSCOPE_BASE_URL}) == (
            oa.DEFAULT_DASHSCOPE_BASE_URL,
            "config-dashscope",
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "env-openrouter")
    for resolve in (oa.resolve_openai_endpoint, omni.resolve_omni_endpoint):
        assert resolve({}) == ("https://openrouter.ai/api/v1", "env-openrouter")


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


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "base_url",
    [
        "https://openrouter.ai/api/v1",
        "http://localhost:8000/v1",
        oa.DEFAULT_DASHSCOPE_BASE_URL,
        "https://api.orcarouter.ai/v1",
    ],
)
def test_clients_send_video_frames_as_ordered_images(monkeypatch, streaming, base_url):
    frame_urls = [f"https://example.com/frame{i}.jpg" for i in range(4)]
    other_parts = [
        {"type": "input_audio", "input_audio": {"data": "YQ==", "format": "wav"}},
        {"type": "video_url", "video_url": {"url": "https://example.com/clip.mp4"}},
        {"type": "text", "text": "Describe the videos."},
    ]
    messages = [
        {"role": "system", "content": "Describe media."},
        {
            "role": "user",
            "content": [
                {"type": "video", "video": frame_urls[:2], "fps": 2},
                {"type": "video", "video": frame_urls[2:]},
                *other_parts,
            ],
        },
    ]
    original = deepcopy(messages)
    chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])
    holder = _install_fake_openai(monkeypatch, lambda _: [chunk] if streaming else "ok")
    call = omni.call_omni if streaming else oa.call_openai_chat
    call(base_url=base_url, api_key="test-key", model="test-model", messages=messages)
    sent = holder["client"].chat.completions.seen[0]["messages"]

    assert messages == original
    assert sent[0] == original[0]
    assert sent[1]["role"] == "user"
    parts = sent[1]["content"]
    assert not any(part["type"] == "video" for part in parts)
    assert [part["image_url"]["url"] for part in parts if part["type"] == "image_url"] == frame_urls
    assert parts[-3:] == other_parts
    assert "2 fps" in parts[0]["text"]
    assert parts[3]["type"] == "text"


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


def _status_error(status_code, message="Extra inputs are not permitted"):
    import openai

    req = httpx.Request("POST", "http://local/v1")
    return openai.APIStatusError(message, response=httpx.Response(status_code, request=req), body=None)


@pytest.mark.parametrize("status_code", [400, 422])
def test_call_openai_chat_drops_optional_extra_body_on_validation_error(monkeypatch, status_code):
    """Request-validation failures retry once without optional hints."""

    def behavior(n):
        if n == 1:
            raise _status_error(status_code)
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
    assert len(seen) == 2  # validation is not retried unchanged; only the hint-free call follows
    assert seen[0]["extra_body"] == {"enable_thinking": False}
    assert "extra_body" not in seen[1]


def test_call_openai_chat_keeps_base_extra_body_when_dropping_hints(monkeypatch):
    """Only optional fields are dropped; the base extra_body survives."""

    def behavior(n):
        if n == 1:
            raise _status_error(400)
        return "RESULT"

    holder = _install_fake_openai(monkeypatch, behavior)
    out = oa.call_openai_chat(
        base_url="http://local",
        api_key="k",
        max_retries=3,
        model="m",
        messages=[],
        extra_body={"modalities": ["text"], "priority": "base"},
        optional_extra_body={"enable_thinking": False, "priority": "hint"},
    )
    seen = holder["client"].chat.completions.seen
    assert out == "RESULT"
    assert seen[0]["extra_body"] == {
        "modalities": ["text"],
        "enable_thinking": False,
        "priority": "base",
    }
    assert seen[1]["extra_body"] == {"modalities": ["text"], "priority": "base"}


def test_call_openai_chat_400_without_hints_is_not_retried(monkeypatch):
    """A 400 with nothing droppable is the caller's error and propagates on the first attempt."""
    import openai

    def behavior(n):
        raise _status_error(400, "image is not a valid input")

    holder = _install_fake_openai(monkeypatch, behavior)
    with pytest.raises(openai.APIStatusError, match="image is not a valid input"):
        oa.call_openai_chat(base_url="http://local", api_key="k", max_retries=3, model="m", messages=[])
    assert holder["client"].chat.completions.calls == 1


@pytest.mark.parametrize(("status_code", "is_transient"), [(401, False), (429, True), (503, True)])
def test_call_openai_chat_non_validation_status_keeps_hints(monkeypatch, status_code, is_transient):
    """Non-validation failures never switch to the base request."""
    import openai

    def behavior(n):
        if n == 1:
            raise _status_error(status_code, "request failed")
        return "RESULT"

    holder = _install_fake_openai(monkeypatch, behavior)

    def call():
        return oa.call_openai_chat(
            base_url="http://local",
            api_key="k",
            model="m",
            messages=[],
            optional_extra_body={"enable_thinking": False},
        )

    if is_transient:
        assert call() == "RESULT"
    else:
        with pytest.raises(openai.APIStatusError, match="request failed"):
            call()

    seen = holder["client"].chat.completions.seen
    assert len(seen) == (2 if is_transient else 1)
    assert all(call["extra_body"] == {"enable_thinking": False} for call in seen)


def test_call_openai_chat_400_after_dropping_hints_propagates(monkeypatch):
    """When the hint was not the cause, the second 400 surfaces rather than the first."""
    import openai

    def behavior(n):
        message = "model does not exist" if n > 1 else "Extra inputs are not permitted"
        raise _status_error(400, message)

    holder = _install_fake_openai(monkeypatch, behavior)
    with pytest.raises(openai.APIStatusError, match="model does not exist"):
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


def test_retry_log_omits_provider_error_message(monkeypatch, caplog):
    monkeypatch.setattr(sr.time, "sleep", lambda *_: None)

    class EchoedRequestError(RuntimeError):
        status_code = 500

    def fail():
        raise EchoedRequestError("data:image/png;base64,SECRET_BASE64_PAYLOAD")

    with pytest.raises(EchoedRequestError):
        sr.retry_call(fail, attempts=2)

    assert "EchoedRequestError (HTTP 500)" in caplog.text
    assert "SECRET_BASE64_PAYLOAD" not in caplog.text


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
