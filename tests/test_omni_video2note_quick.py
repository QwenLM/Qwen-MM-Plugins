"""Offline coverage for bounded local screenshot selection and note normalization."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from test_omni_video2note import import_pipeline_module


@pytest.fixture
def timeline_case(tmp_path):
    schemas = import_pipeline_module("schemas")
    video = tmp_path / "input.mp4"
    video.write_bytes(b"frame extraction is mocked")
    config = SimpleNamespace(video=video, warnings=[])
    probe = schemas.ProbeResult(
        path=str(video),
        size_bytes=video.stat().st_size,
        duration=12.0,
        width=640,
        height=360,
        fps=25.0,
        video_codec="test",
        has_audio=True,
    )
    plan = schemas.DocumentPlan(
        title="Task",
        audience="Reader",
        overview_goal="Finish the task",
        steps=[
            schemas.PlanStep(
                id=index + 1,
                title=f"Step {index + 1}",
                objective="Observe the action",
                start=1.0 + index * 5,
                end=4.0 + index * 5,
                visual_targets=[schemas.VisualTarget("main", "primary", "the action")],
            )
            for index in range(2)
        ],
    )
    return config, plan, probe, tmp_path / "work"


def _fake_extractor(calls, *, fail_before=None):
    def extract(_video, timestamp, path, **kwargs):
        calls.append((timestamp, kwargs))
        if fail_before is not None and timestamp < fail_before:
            raise RuntimeError("simulated extraction failure")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 360), "gray").save(path)
        return path

    return extract


def _good_metrics(_path, **_kwargs):
    return SimpleNamespace(score=0.8, reject_reasons=())


def test_quick_selection_bounds_candidates_and_uses_only_step_times(timeline_case, monkeypatch):
    quick = import_pipeline_module("quick_selection")
    calls = []
    monkeypatch.setattr(quick, "extract_frame", _fake_extractor(calls))
    monkeypatch.setattr(quick, "evaluate_image", _good_metrics)
    config, plan, probe, workdir = timeline_case

    result = quick.select_timeline_frames(config, plan, probe, workdir)

    assert len(calls) == 6
    assert all(0 < kwargs["timeout"] <= 4 for _, kwargs in calls)
    assert all(kwargs["_probe_result"] is probe for _, kwargs in calls)
    assert all(any(step.start <= time <= step.end for step in plan.steps) for time, _ in calls)
    assert len(result.selections) == 2
    assert len(result.tried) == 6
    for step, selection in zip(plan.steps, result.selections):
        assert len(selection.choices) == 1
        choice = selection.choices[0]
        assert step.start <= choice.timestamp <= step.end
        assert (workdir / choice.frame_path).is_file()
        assert choice.relevance == 0.0
        assert "semantic relevance was not evaluated" in choice.reason
        assert step.title in choice.caption
    assert config.warnings == []
    result.validate()


def test_quick_selection_extraction_failure_is_isolated_to_one_step(timeline_case, monkeypatch):
    quick = import_pipeline_module("quick_selection")
    monkeypatch.setattr(quick, "extract_frame", _fake_extractor([], fail_before=5))
    monkeypatch.setattr(quick, "evaluate_image", _good_metrics)
    config, plan, probe, workdir = timeline_case

    result = quick.select_timeline_frames(config, plan, probe, workdir)

    assert not result.selections[0].choices
    assert result.selections[0].misses["main"]
    assert len(result.selections[1].choices) == 1
    assert len(config.warnings) == 1
    assert "Text was preserved" in config.warnings[0]


def test_quick_selection_keeps_readable_frames_when_quality_fails(timeline_case, monkeypatch):
    quick = import_pipeline_module("quick_selection")
    monkeypatch.setattr(quick, "extract_frame", _fake_extractor([]))

    def fail_quality(*_args, **_kwargs):
        raise RuntimeError("simulated metric failure")

    monkeypatch.setattr(quick, "evaluate_image", fail_quality)
    config, plan, probe, workdir = timeline_case
    result = quick.select_timeline_frames(config, plan, probe, workdir)

    assert all(len(selection.choices) == 1 for selection in result.selections)
    assert all("quality metrics unavailable" in warning for warning in config.warnings)
    assert len(config.warnings) == 2


def test_quick_selection_does_not_gate_on_quality_score(timeline_case, monkeypatch):
    quick = import_pipeline_module("quick_selection")
    monkeypatch.setattr(quick, "extract_frame", _fake_extractor([]))
    monkeypatch.setattr(
        quick, "evaluate_image", lambda *_args, **_kwargs: SimpleNamespace(score=0.0, reject_reasons=("blur",))
    )
    config, plan, probe, workdir = timeline_case
    result = quick.select_timeline_frames(config, plan, probe, workdir)

    assert all(selection.choices for selection in result.selections)
    assert all("limited image clarity" in warning for warning in config.warnings)


def test_quick_selection_expired_local_budget_skips_remaining_frames(timeline_case, monkeypatch):
    quick = import_pipeline_module("quick_selection")
    calls = []
    ticks = iter([0.0])
    monkeypatch.setattr(quick.time, "monotonic", lambda: next(ticks, 13.0))
    monkeypatch.setattr(quick, "extract_frame", _fake_extractor(calls))
    config, plan, probe, workdir = timeline_case
    result = quick.select_timeline_frames(config, plan, probe, workdir)

    assert calls == []
    assert all(not selection.choices for selection in result.selections)
    assert len(config.warnings) == 2


def test_quick_selection_never_borrows_frames_for_out_of_range_steps(timeline_case, monkeypatch):
    quick = import_pipeline_module("quick_selection")
    calls = []
    monkeypatch.setattr(quick, "extract_frame", _fake_extractor(calls))
    config, plan, probe, workdir = timeline_case
    plan.steps = plan.steps[:1]
    plan.steps[0].start = probe.duration + 1
    plan.steps[0].end = probe.duration + 2

    result = quick.select_timeline_frames(config, plan, probe, workdir)

    assert calls == []
    assert result.selections[0].choices == []
    assert result.selections[0].misses["main"]


def test_local_builder_normalizes_ids_times_and_list_fields():
    builder = import_pipeline_module("note_builder")
    schemas = import_pipeline_module("schemas")
    understanding = schemas.VideoUnderstanding(
        language="en",
        subject="Observed task",
        summary="An observed demonstration.",
        events=[schemas.TimedEvent(0.0, 2.0, "Open the panel.")],
        safety=["Handle with care."],
    )
    raw = {
        "title": "Useful note",
        "tools": "one tool",
        "safety": None,
        "steps": [
            {
                "id": "wrong id",
                "title": "Open",
                "instruction": "Open the panel.",
                "start": "00:00.5",
                "end": "2s",
                "details": "Observe the control.",
            }
        ],
    }
    plan, draft = builder.build_document(raw, understanding, duration=2.0, max_steps=8)

    assert plan.steps[0].id == draft.steps[0].id == 1
    assert (plan.steps[0].start, plan.steps[0].end) == (0.5, 2.0)
    assert draft.steps[0].details == ["Observe the control."]
    assert draft.tools == ["one tool"]
    assert draft.safety == ["Handle with care."]
    draft.validate_against(plan)
