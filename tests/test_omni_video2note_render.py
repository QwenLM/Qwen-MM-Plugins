"""Offline four-phase integration tests for Omni Video2Note rendering."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from test_omni_video2note import import_pipeline_module, make_short_av_video

runner = import_pipeline_module("runner")
schemas = import_pipeline_module("schemas")
rendering = import_pipeline_module("rendering")

HAS_PDF_RASTERIZER = importlib.util.find_spec("pypdfium2") is not None or shutil.which("pdftoppm") is not None


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
    monkeypatch.setattr(runner.model_gateway, "propose_repairs", lambda *_args, **_kwargs: [])


def _run(tmp_path: Path, monkeypatch, *, review_unavailable: bool = False):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    _install_model_mocks(monkeypatch, review_unavailable=review_unavailable)
    result = runner.run_video2note(
        video_path=video,
        output_path=output,
        language="en",
        quality_profile="fast",
        max_iterations=1,
        no_asr=True,
    )
    return result, output


def test_mocked_four_phase_pipeline_produces_openable_pdf(tmp_path: Path, monkeypatch):
    result, output = _run(tmp_path, monkeypatch)
    assert output.read_bytes().startswith(b"%PDF-")
    assert rendering.pdf_page_count(output) >= 1
    if HAS_PDF_RASTERIZER:
        assert result["exit_code"] == 0
        assert result["status"] == "pass"
    else:
        # Rendering succeeds, but deterministic PDF audit cannot pass without a page rasterizer.
        assert result["exit_code"] == 2
        assert result["status"] == "best_effort"


def test_review_unavailable_returns_valid_best_effort_pdf(tmp_path: Path, monkeypatch):
    result, output = _run(tmp_path, monkeypatch, review_unavailable=True)
    assert result["exit_code"] == 2
    assert result["status"] == "best_effort"
    assert "review unavailable" in result["error"]
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
        max_iterations=1,
        no_asr=True,
    )
    assert result["exit_code"] == 1
    assert result["status"] == "failed"
    assert "no PDF" in result["error"]
    assert not output.exists()


def test_unaudited_candidate_is_never_published_and_job_stays_resumable(tmp_path: Path, monkeypatch):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    output.write_bytes(b"%PDF-1.4 stale document\n%%EOF\n")
    _install_model_mocks(monkeypatch)
    monkeypatch.setattr(
        runner,
        "audit_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit tooling crashed")),
    )

    result = runner.run_video2note(
        video_path=video,
        output_path=output,
        language="en",
        quality_profile="fast",
        max_iterations=1,
        no_asr=True,
        overwrite=True,
    )
    workdir = Path(result["workdir"])
    assert result["exit_code"] == 1
    assert result["status"] == "failed"
    assert result["resumable"] is True
    # The rendered candidate was never audited, so nothing may be published — not even the PDF the
    # overwrite run took over.
    assert not output.exists()
    assert not (workdir / "phase4" / "best.pdf").exists()
    assert sorted((workdir / "phase4").glob("iteration-*/candidate.pdf"))
    status = runner.get_status(workdir=workdir)
    assert status["status"] == "render_review_repair"
    assert status["resumable"] is True
    assert status["final_pdf"] is None


def test_audited_candidate_is_recovered_as_best_effort(tmp_path: Path, monkeypatch):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    _install_model_mocks(monkeypatch, demand_repair=True)
    real_render = runner.render_document
    calls: list[int] = []

    def flaky_render(*args, **kwargs):
        calls.append(len(calls) + 1)
        if len(calls) > 1:
            raise RuntimeError("renderer crashed while repairing")
        return real_render(*args, **kwargs)

    monkeypatch.setattr(runner, "render_document", flaky_render)

    result = runner.run_video2note(
        video_path=video,
        output_path=output,
        language="en",
        quality_profile="fast",
        max_iterations=2,
        no_asr=True,
    )
    assert len(calls) == 2
    assert result["exit_code"] == 2
    assert result["status"] == "best_effort"
    assert result["best_iteration"] == 1
    assert result["iterations"] == 1
    assert "renderer crashed" in result["error"]
    assert output.read_bytes().startswith(b"%PDF-")
    assert rendering.pdf_page_count(output) >= 1
    status = runner.get_status(workdir=Path(result["workdir"]))
    assert status["status"] == "best_effort"
    assert status["passed"] is False
