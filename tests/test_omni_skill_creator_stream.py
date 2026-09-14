"""Creator SSE transport: real httpx serialization, no external service calls."""

import json
from unittest.mock import Mock

import httpx
import pytest

from qwen_mm_plugins_omni_skill_creator.tools import read_native_av as av


def sse(*events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"


@pytest.fixture
def server(monkeypatch):
    requests = []
    options = []
    responses = []
    client_type = httpx.Client
    sleep = Mock()
    monkeypatch.setattr(av.time, "sleep", sleep)

    def client(**kwargs):
        options.append(kwargs)

        def respond(request):
            requests.append(json.loads(request.content))
            status, body = responses.pop(0) if len(responses) > 1 else responses[0]
            return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})

        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(av.httpx, "Client", client)
    return requests, options, responses, sleep


def invoke(**kwargs):
    return av._openai_call(
        "qwen3.5-omni-plus",
        "https://media.example/video.mp4",
        "same prompt",
        base="https://dashscope.example/v1",
        api_key="test-key",
        **kwargs,
    )


def test_sse_keeps_content_reasoning_and_final_usage(server):
    requests, options, responses, _sleep = server
    responses.append(
        (
            200,
            sse(
                {"choices": [{"delta": {"reasoning_content": "reasoning "}}]},
                {"choices": [{"delta": {"reasoning_content": "kept", "content": "Hello "}}]},
                {"choices": [{"delta": {"content": "世界"}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 123, "completion_tokens": 8, "total_tokens": 131}},
            ),
        )
    )

    result = invoke(timeout=17, max_output_tokens=100)

    assert result.text == "Hello 世界"
    assert result.reasoning == "reasoning kept"
    assert result.usage == {"prompt_tokens": 123, "completion_tokens": 8, "total_tokens": 131}
    assert options == [{"timeout": 17}]
    assert requests[0]["stream"] is True
    assert requests[0]["modalities"] == ["text"]
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert requests[0]["max_tokens"] == 100
    assert requests[0]["messages"][0]["content"][-1]["text"] == "same prompt"


def test_sse_comments_multiline_data_and_done():
    lines = [
        ": heartbeat",
        "event: message",
        'data: {"choices": [],',
        'data: "usage": {"prompt_tokens": 5}}',
        "",
        "data: [DONE]",
        "",
        "data: bad json",
        "",
    ]
    assert list(av._sse_payloads(iter(lines))) == [{"choices": [], "usage": {"prompt_tokens": 5}}]


@pytest.mark.parametrize("status, attempts", [(400, 1), (401, 1), (403, 1), (429, 3), (500, 3)])
def test_stream_retries_only_transient_http_errors(server, status, attempts):
    requests, _options, responses, sleep = server
    responses.append((status, '{"error":"offline test"}'))
    with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
        invoke()
    assert len(requests) == attempts
    assert sleep.call_count == attempts - 1


@pytest.mark.parametrize(
    "status, body, recovery",
    [
        (401, '{"error":{"code":"InvalidApiKey","message":"invalid key"}}', "Authentication or permission failed"),
        (
            403,
            '{"error":{"code":"AccessDenied","message":"model access denied"}}',
            "Authentication or permission failed",
        ),
        (
            429,
            '{"error":{"code":"Throttling.AllocationQuota","message":"allocation quota exceeded"}}',
            "Quota or account balance is exhausted",
        ),
    ],
)
def test_terminal_provider_errors_stop_and_give_specific_recovery(server, monkeypatch, status, body, recovery):
    requests, _options, responses, sleep = server
    responses.append((status, body))
    monkeypatch.setattr(av, "_default_openai_base", lambda: "https://dashscope.example/v1")

    text = av.handle({"video_path": "https://media.example/video.mp4"})[0]["text"]

    assert text.startswith("Error: read_native_av failed: FATAL:")
    assert recovery in text
    assert "Do not retry this call or split the video" in text
    assert "retry this SAME whole-video call" not in text
    assert len(requests) == 1
    sleep.assert_not_called()


def test_transient_provider_failure_has_bounded_retry_recovery(server, monkeypatch):
    requests, _options, responses, sleep = server
    responses.append((503, '{"error":{"code":"ServiceUnavailable","message":"temporary error"}}'))
    monkeypatch.setattr(av, "_default_openai_base", lambda: "https://dashscope.example/v1")

    text = av.handle({"video_path": "https://media.example/video.mp4"})[0]["text"]

    assert "Temporary rate limit or service failure persisted after bounded retries" in text
    assert "splitting the video does not resolve this error" in text
    assert "retry this SAME whole-video call" not in text
    assert len(requests) == 3
    assert sleep.call_count == 2


def test_stream_retry_does_not_duplicate_partial_text(server):
    requests, _options, responses, _sleep = server
    responses.extend(
        [
            (200, sse({"choices": [{"delta": {"content": "discard partial"}}]}, {"error": "temporary error"})),
            (200, sse({"choices": [{"delta": {"content": "complete answer"}, "finish_reason": "stop"}]})),
        ]
    )
    assert invoke().text == "complete answer"
    assert len(requests) == 2


@pytest.mark.parametrize(
    "error, attempts",
    [
        ({"code": "InvalidApiKey", "message": "invalid key"}, 1),
        ({"code": "Throttling.AllocationQuota", "message": "allocation quota exceeded"}, 1),
        ({"code": "Throttling.RateQuota", "message": "rate limit"}, 3),
    ],
)
def test_stream_error_event_uses_the_same_retry_classification(server, error, attempts):
    requests, _options, responses, sleep = server
    responses.append((200, sse({"error": error})))

    with pytest.raises(av._ProviderError):
        invoke()

    assert len(requests) == attempts
    assert sleep.call_count == attempts - 1


def test_reasoning_only_length_stop_is_fatal(server):
    requests, _options, responses, sleep = server
    responses.append(
        (
            200,
            sse(
                {"choices": [{"delta": {"reasoning_content": "thinking"}, "finish_reason": "length"}]},
                {"choices": [], "usage": {"reasoning_tokens": 16}},
            ),
        )
    )
    with pytest.raises(RuntimeError, match="FATAL: max_output_tokens=16"):
        invoke(max_output_tokens=16)
    assert len(requests) == 1
    sleep.assert_not_called()


def test_text_length_stop_is_preserved_for_tool_notice(server):
    requests, _options, responses, sleep = server
    responses.append(
        (
            200,
            sse(
                {"choices": [{"delta": {"content": "partial answer"}, "finish_reason": "length"}]},
                {"choices": [], "usage": {"completion_tokens": 64}},
            ),
        )
    )

    result = invoke(max_output_tokens=32768)

    assert result.text == "partial answer"
    assert result.finish_reason == "length"
    assert len(requests) == 1
    sleep.assert_not_called()


def test_handle_appends_one_length_notice_after_existing_text(server, monkeypatch):
    requests, _options, responses, sleep = server
    responses.append(
        (
            200,
            sse({"choices": [{"delta": {"content": "partial answer"}, "finish_reason": "length"}]}),
        )
    )
    monkeypatch.setattr(av, "_default_openai_base", lambda: "https://dashscope.example/v1")

    text = av.handle({"video_path": "https://media.example/video.mp4"})[0]["text"]

    assert "partial answer\n\n[INCOMPLETE RESPONSE — NOT EVENT LOG CONTENT:" in text
    assert "finish_reason=length" in text
    assert "reread the source in shorter contiguous ranges with the same prompt" in text
    assert "Do not copy this notice into the event log or transcript." in text
    assert text.count("[INCOMPLETE RESPONSE — NOT EVENT LOG CONTENT:") == 1
    assert len(requests) == 1
    sleep.assert_not_called()


def test_handle_does_not_append_length_notice_after_normal_stop(server, monkeypatch):
    requests, _options, responses, _sleep = server
    responses.append((200, sse({"choices": [{"delta": {"content": "complete"}, "finish_reason": "stop"}]})))
    monkeypatch.setattr(av, "_default_openai_base", lambda: "https://dashscope.example/v1")

    text = av.handle({"video_path": "https://media.example/video.mp4"})[0]["text"]

    assert text.endswith("complete")
    assert "INCOMPLETE RESPONSE" not in text
    assert len(requests) == 1
