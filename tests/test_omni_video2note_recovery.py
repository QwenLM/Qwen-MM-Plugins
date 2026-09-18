"""Offline evidence recovery after malformed or interrupted Omni responses."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_omni_video2note import import_pipeline_module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    monkeypatch.setattr(
        gateway,
        "call_omni_text",
        lambda *_args, **_kwargs: pytest.fail("unexpected live Omni request"),
    )


@pytest.fixture
def recovery_case():
    schemas = import_pipeline_module("schemas")
    media = import_pipeline_module("media")
    config = SimpleNamespace(
        language="zh",
        title=None,
        omni_model="test-omni",
        no_asr=False,
        require_asr=False,
        deadline=time.monotonic() + 150,
        _source_duration=60.0,
        warnings=[],
    )
    probe = schemas.ProbeResult(
        path="/offline/source.mp4",
        size_bytes=100,
        duration=60.0,
        width=640,
        height=360,
        fps=25.0,
        video_codec="test",
        has_audio=True,
    )
    chunks = [
        media.MediaChunk(
            Path(f"/offline/chunk-{index}.mp4"),
            index * 20.0,
            (index + 1) * 20.0,
            {"kind": "inline", "source": f"https://example.invalid/chunk-{index}.mp4"},
        )
        for index in range(3)
    ]
    understanding = schemas.VideoUnderstanding(
        language="zh",
        subject="给猫剪指甲",
        summary="视频演示固定猫爪后剪去透明尖端。",
        events=[
            schemas.TimedEvent(start=2.0, end=8.0, fact="先固定猫爪。"),
            schemas.TimedEvent(start=12.0, end=18.0, fact="剪去指甲的透明尖端。"),
        ],
        safety=["避开血线。"],
    )
    return config, probe, chunks, understanding


def _responses(monkeypatch, values):
    gateway = import_pipeline_module("model_gateway")
    calls = []
    responses = iter(values)

    def respond(*_args, **kwargs):
        calls.append(kwargs)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(gateway, "call_omni_text", respond)
    return calls


def _all_step_text(draft):
    return "\n".join(text for step in draft.steps for text in (step.title, step.instruction, *step.details))


def test_understanding_recovers_complete_evidence_from_interrupted_json(recovery_case, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    error_type = import_pipeline_module("omni_client").OmniCallError
    config, probe, chunks, _understanding = recovery_case
    partial = (
        '{"summary":"固定猫爪后剪去透明尖端。", "events":['
        '{"start":2,"end":8,"fact":"先固定猫爪。"},'
        '{"start":12,"fact":"unfinished'
    )
    calls = _responses(monkeypatch, [error_type("stream interrupted", partial_text=partial)])

    result = gateway.understand_video(config, probe, chunks[:1])

    assert len(calls) == 1
    assert result.summary == "固定猫爪后剪去透明尖端。"
    assert [(event.start, event.end, event.fact) for event in result.events] == [(2.0, 8.0, "先固定猫爪。")]
    assert "unfinished" not in json.dumps(result.to_dict(), ensure_ascii=False)
    assert any("partial response" in warning for warning in config.warnings)


def test_plain_prose_understanding_survives_without_a_format_repair(recovery_case, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    config, probe, chunks, _understanding = recovery_case
    prose = "视频展示给猫修剪指甲：先固定猫爪，然后只剪去透明尖端，避免碰到血线。"
    calls = _responses(monkeypatch, [prose, TimeoutError("offline timeout")])

    understanding = gateway.understand_video(config, probe, chunks[:1])
    plan, draft = gateway.generate_document(config, understanding)

    assert len(calls) == 2
    assert [call["stage"] for call in calls] == ["understand_video", "generate_document"]
    assert understanding.summary == prose
    assert understanding.events == []
    assert draft.overview == prose
    assert draft.steps[0].instruction == prose
    draft.validate_against(plan)


def test_all_failed_chunks_report_no_evidence_and_hide_provider_error(recovery_case, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    config, probe, chunks, _understanding = recovery_case
    secret = "private-provider-detail-do-not-publish"
    calls = _responses(monkeypatch, [TimeoutError(secret)] * len(chunks))

    with pytest.raises(RuntimeError, match="no factual PDF") as error:
        gateway.understand_video(config, probe, chunks)

    assert len(calls) == len(chunks)
    assert secret not in str(error.value)
    assert secret not in " ".join(config.warnings)


def test_partial_chunk_failure_preserves_only_observed_timeline(recovery_case, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    config, probe, chunks, _understanding = recovery_case
    calls = _responses(
        monkeypatch,
        [
            json.dumps({"summary": "先固定猫爪。", "events": [{"start": 2, "end": 8, "fact": "先固定猫爪。"}]}),
            TimeoutError("middle chunk failed"),
            json.dumps({"summary": "完成后放开猫爪。", "events": [{"start": 1, "end": 4, "fact": "完成后放开猫爪。"}]}),
        ],
    )

    result = gateway.understand_video(config, probe, chunks)

    assert len(calls) == 3
    assert [(event.start, event.end) for event in result.events] == [(2.0, 8.0), (41.0, 44.0)]
    assert [event.fact for event in result.events] == ["先固定猫爪。", "完成后放开猫爪。"]
    assert not any(20 <= event.start < 40 for event in result.events)
    assert any("2/3" in warning for warning in config.warnings)


def test_deadline_stops_remaining_chunks_without_losing_completed_evidence(recovery_case, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    config, probe, chunks, _understanding = recovery_case
    calls = []

    def consume_budget(*_args, **kwargs):
        calls.append(kwargs)
        config.deadline = time.monotonic() - 1
        return '{"summary":"先固定猫爪。","events":[{"start":2,"end":8,"fact":"先固定猫爪。"}]}'

    monkeypatch.setattr(gateway, "call_omni_text", consume_budget)
    result = gateway.understand_video(config, probe, chunks)

    assert len(calls) == 1
    assert [event.fact for event in result.events] == ["先固定猫爪。"]
    assert any("20.0" in warning and "60.0" in warning for warning in config.warnings)


@pytest.mark.parametrize("response", [TimeoutError("offline timeout"), "not valid JSON", "", '{"steps":null}'])
def test_failed_note_request_uses_existing_facts_without_repair(recovery_case, monkeypatch, response):
    gateway = import_pipeline_module("model_gateway")
    config, _probe, _chunks, understanding = recovery_case
    calls = _responses(monkeypatch, [response])

    plan, draft = gateway.generate_document(config, understanding)

    assert len(calls) == 1
    assert calls[0]["stage"] == "generate_document"
    assert all(event.fact in _all_step_text(draft) for event in understanding.events)
    assert draft.overview == understanding.summary
    assert draft.safety == understanding.safety
    assert config.warnings
    draft.validate_against(plan)


def test_step_limit_groups_facts_without_dropping_final_events(recovery_case, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    schemas = import_pipeline_module("schemas")
    config, _probe, _chunks, understanding = recovery_case
    understanding.events = [
        schemas.TimedEvent(start=index * 3.0, end=index * 3.0 + 2.0, fact=f"视频中的动作 {index + 1}。")
        for index in range(19)
    ]
    calls = _responses(monkeypatch, [TimeoutError("offline timeout")])

    plan, draft = gateway.generate_document(config, understanding)

    assert len(calls) == 1
    assert 1 <= len(draft.steps) <= 8
    assert all(event.fact in _all_step_text(draft) for event in understanding.events)
    assert understanding.events[-1].fact in _all_step_text(draft)
    assert plan.steps[-1].end == understanding.events[-1].end
    draft.validate_against(plan)


def test_interrupted_note_retains_complete_step_and_normalizes_details(recovery_case, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    error_type = import_pipeline_module("omni_client").OmniCallError
    config, _probe, _chunks, understanding = recovery_case
    partial = (
        '{"title":"给猫剪指甲","overview":"按视频步骤操作。","steps":['
        '{"title":"固定猫爪","start":"2","end":"8","instruction":"先固定猫爪。","details":"保持固定"},'
        '{"title":"unfinished'
    )
    calls = _responses(monkeypatch, [error_type("stream interrupted", partial_text=partial)])

    plan, draft = gateway.generate_document(config, understanding)

    assert len(calls) == 1
    assert draft.steps[0].instruction == "先固定猫爪。"
    assert draft.steps[0].details == ["保持固定"]
    assert "unfinished" not in _all_step_text(draft)
    draft.validate_against(plan)


@pytest.mark.parametrize(
    "response",
    [
        '{"error":{"message":"authentication failed"}}',
        "HTTP 401: unauthorized request; check the provided API key.",
        '{"summary":"unfinished',
    ],
)
def test_provider_errors_or_unfinished_fields_are_not_video_facts(recovery_case, monkeypatch, response):
    gateway = import_pipeline_module("model_gateway")
    config, probe, chunks, _understanding = recovery_case
    calls = _responses(monkeypatch, [response])

    with pytest.raises(RuntimeError, match="no factual PDF"):
        gateway.understand_video(config, probe, chunks[:1])

    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_permission_denial_stops_later_chunks(recovery_case, monkeypatch, status):
    gateway = import_pipeline_module("model_gateway")
    error_type = import_pipeline_module("omni_client").OmniCallError
    config, probe, chunks, _understanding = recovery_case
    calls = _responses(monkeypatch, [error_type("private provider detail", status_code=status)])

    with pytest.raises(RuntimeError, match=rf"access denied \(HTTP {status}\)") as error:
        gateway.understand_video(config, probe, chunks)

    assert len(calls) == 1
    assert "private provider detail" not in str(error.value)
    assert any("20.0-60.0s" in warning for warning in config.warnings)
