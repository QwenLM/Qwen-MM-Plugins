"""Offline four-phase integration tests for Omni Video2Note rendering."""

from __future__ import annotations

from pathlib import Path

from test_omni_video2note import import_pipeline_module, make_short_av_video

runner = import_pipeline_module("runner")
schemas = import_pipeline_module("schemas")
rendering = import_pipeline_module("rendering")


def _install_model_mocks(monkeypatch, *, review_unavailable: bool = False, demand_repair: bool = False) -> None:
    def understand_video(config, probe, chunks):
        assert chunks
        return schemas.VideoUnderstanding(
            language=config.language,
            subject="Button tutorial",
            summary="The video demonstrates one visible action.",
            events=[schemas.TimedEvent(0.0, min(1.0, probe.duration), "A control is shown and activated.")],
            audience="beginners",
            visible_terms=["control"],
        )

    def plan_document(_config, understanding, **_kwargs):
        assert understanding.events
        return schemas.DocumentPlan(
            title="Use the control",
            audience="beginners",
            overview_goal="Activate the visible control.",
            steps=[
                schemas.PlanStep(
                    id=1,
                    title="Activate the control",
                    objective="Locate and activate the control shown in the video.",
                    start=0.0,
                    end=min(1.5, understanding.events[-1].end + 0.5),
                    visual_targets=[schemas.VisualTarget("control", "primary", "the visible control")],
                )
            ],
        )

    def write_document(_config, plan, _understanding, **_kwargs):
        return schemas.DocumentDraft(
            title=plan.title,
            overview="Follow the single demonstrated step.",
            steps=[
                schemas.DraftStep(
                    id=1,
                    title="Activate the control",
                    instruction="Locate the control and activate it once.",
                    details=["Confirm that the control changes state."],
                    caption="The control before activation",
                )
            ],
            closing="The demonstrated action is complete.",
        )

    def review_candidates(_config, requests):
        reviews = []
        for request in requests:
            candidates = request["candidates"]
            assert candidates, "synthetic test video should yield quality-approved frame candidates"
            selected = candidates[0]["id"]
            for target in request["step"]["visual_targets"]:
                reviews.append(
                    schemas.CandidateReview(
                        step_id=request["step"]["id"],
                        target_id=target["id"],
                        selected_id=selected,
                        relevance=0.99,
                        reason="clear visual evidence",
                        caption="Selected evidence",
                        ranked_ids=[selected],
                    )
                )
        return reviews

    def review_pdf(_config, _pdf_path, audit, **_kwargs):
        if review_unavailable:
            raise RuntimeError("offline reviewer intentionally unavailable")
        if demand_repair:
            return schemas.ReviewReport(
                verdict="repair",
                overall=6.0,
                scores={
                    "accuracy": 6.0,
                    "completeness": 6.0,
                    "clarity": 6.0,
                    "visual_quality": 6.0,
                },
                summary="the reviewer asked for one more repair round.",
            )
        return schemas.ReviewReport(
            verdict="pass",
            overall=9.0,
            scores={
                "accuracy": 9.0,
                "completeness": 9.0,
                "clarity": 9.0,
                "visual_quality": 9.0,
            },
            summary="All gates passed.",
        )

    monkeypatch.setattr(runner.model_gateway, "understand_video", understand_video)
    monkeypatch.setattr(runner.model_gateway, "plan_document", plan_document)
    monkeypatch.setattr(runner.model_gateway, "write_document", write_document)
    monkeypatch.setattr(runner.model_gateway, "review_candidates", review_candidates)
    monkeypatch.setattr(runner.model_gateway, "review_pdf", review_pdf)


def _run(tmp_path: Path, monkeypatch, *, review_unavailable: bool = False):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    _install_model_mocks(monkeypatch, review_unavailable=review_unavailable)
    result = runner.run_video2note(
        video_path=video,
        output_path=output,
        language="en",
        quality_profile="fast",
        no_asr=True,
    )
    return result, output


def test_mocked_four_phase_pipeline_produces_openable_pdf(tmp_path: Path, monkeypatch):
    result, output = _run(tmp_path, monkeypatch)
    assert output.read_bytes().startswith(b"%PDF-")
    assert rendering.pdf_page_count(output) >= 1
    assert result["exit_code"] == 0
    assert result["status"] == "complete"
    assert result["images"] >= 1
    assert not output.with_suffix(".pdf.work").exists()
    assert not list(tmp_path.glob("omni-video2note-*"))


def test_review_unavailable_still_delivers_pdf(tmp_path: Path, monkeypatch):
    result, output = _run(tmp_path, monkeypatch, review_unavailable=True)
    assert result["exit_code"] == 0
    assert result["status"] == "complete"
    assert result["review"] is None
    assert any("review unavailable" in warning for warning in result["warnings"])
    assert output.read_bytes().startswith(b"%PDF-")
    assert rendering.pdf_page_count(output) >= 1


def test_no_rendered_pdf_is_hard_failure(tmp_path: Path, monkeypatch):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    _install_model_mocks(monkeypatch)
    monkeypatch.setattr(
        runner,
        "render_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("renderer produced no PDF")),
    )

    result = runner.run_video2note(
        video_path=video,
        output_path=output,
        language="en",
        quality_profile="fast",
        no_asr=True,
    )
    assert result["exit_code"] == 1
    assert result["status"] == "failed"
    assert "no PDF" in result["error"]
    assert not output.exists()


def test_checks_unavailable_still_delivers_readable_pdf(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        runner, "audit_pdf", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit offline"))
    )
    result, output = _run(tmp_path, monkeypatch)
    assert result["status"] == "complete"
    assert result["audit"] is None
    assert any("audit offline" in warning for warning in result["warnings"])
    assert rendering.pdf_page_count(output) >= 1


def test_review_feedback_does_not_start_a_repair_loop(tmp_path: Path, monkeypatch):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    _install_model_mocks(monkeypatch, demand_repair=True)
    real_render = runner.render_document
    calls = []

    def render_once(*args, **kwargs):
        calls.append(True)
        assert len(calls) == 1
        return real_render(*args, **kwargs)

    monkeypatch.setattr(runner, "render_document", render_once)
    result = runner.run_video2note(video_path=video, output_path=output, language="en", quality_profile="fast")
    assert result["status"] == "complete"
    assert result["review"]["verdict"] == "repair"
    assert result["warnings"]
    assert len(calls) == 1
    assert rendering.pdf_page_count(output) >= 1


def test_failed_call_can_be_retried_without_state_flags(tmp_path: Path, monkeypatch):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    old_workdir = output.with_suffix(".pdf.work")
    old_workdir.mkdir()
    marker = old_workdir / "manifest.json"
    marker.write_text('{"status":"failed"}')
    _install_model_mocks(monkeypatch)
    monkeypatch.setattr(
        runner.model_gateway, "understand_video", lambda *_args: (_ for _ in ()).throw(ValueError("bad model response"))
    )
    arguments = dict(video_path=video, output_path=output, language="en", quality_profile="fast")
    failed = runner.run_video2note(**arguments)
    assert failed["status"] == "failed"
    assert not output.exists()
    assert not list(tmp_path.glob("omni-video2note-*"))

    _install_model_mocks(monkeypatch)
    result = runner.run_video2note(**arguments)
    assert result["status"] == "complete"
    assert rendering.pdf_page_count(output) >= 1
    assert marker.read_text() == '{"status":"failed"}'
    assert not list(tmp_path.glob("omni-video2note-*"))


def test_failed_overwrite_preserves_previous_pdf(tmp_path: Path, monkeypatch):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    previous = b"existing PDF contents"
    output.write_bytes(previous)
    _install_model_mocks(monkeypatch)
    monkeypatch.setattr(
        runner, "render_document", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("render failed"))
    )
    result = runner.run_video2note(video_path=video, output_path=output, overwrite=True, quality_profile="fast")
    assert result["status"] == "failed"
    assert output.read_bytes() == previous


def test_screenshot_field_drift_completes_the_full_pipeline(tmp_path: Path, monkeypatch):
    from test_omni_video2note import _understanding_payload

    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    understand_video = runner.model_gateway.understand_video
    _install_model_mocks(monkeypatch)
    monkeypatch.setattr(runner.model_gateway, "understand_video", understand_video)
    monkeypatch.setattr(runner.model_gateway, "call_omni_json", lambda **_kwargs: _understanding_payload())
    result = runner.run_video2note(
        video_path=video, output_path=output, title="Cat nail tutorial", language="en", quality_profile="fast"
    )
    assert result["status"] == "complete"
    assert result["title"] == "Cat nail tutorial"
    assert result["images"] >= 1
    assert rendering.pdf_page_count(output) >= 1
