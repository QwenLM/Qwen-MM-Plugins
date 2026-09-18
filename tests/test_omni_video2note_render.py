"""Offline integration coverage for two-call, fault-tolerant PDF generation."""

from __future__ import annotations

import importlib.util
import json

import pytest
from test_omni_video2note import import_pipeline_module, make_short_av_video

runner = import_pipeline_module("runner")
rendering = import_pipeline_module("rendering")

# Reading text back out of the PDF needs a text-based renderer; the Pillow fallback rasterizes it.
HAS_TEXT_RENDERER = any(importlib.util.find_spec(name) is not None for name in ("weasyprint", "reportlab"))


def _understanding():
    return {
        "language": "en",
        "subject": "Use the control",
        "summary": "The video shows how to activate a control.",
        "events": [{"start": 0, "end": 1, "fact": "Locate the control and activate it once."}],
        "audience": "beginners",
    }


def _note():
    return {
        "title": "Use the control",
        "overview": "Follow the demonstrated action.",
        "steps": [
            {
                "title": "Activate the control",
                "start": 0,
                "end": 1,
                "instruction": "Locate the control and activate it once.",
                "details": ["Observe the control."],
                "caption": "Video screenshot",
            }
        ],
    }


def _install_model_mocks(monkeypatch, *, writing_failure=False, malformed=False):
    calls = []

    def respond(config, *, stage, **kwargs):
        calls.append((stage, config.omni_model))
        if stage == "understand_video":
            return json.dumps(_understanding())
        assert stage == "generate_document", "no separate plan, selection, or PDF review requests"
        if writing_failure:
            raise TimeoutError("request unavailable")
        return '{"steps": [' if malformed else json.dumps(_note())

    monkeypatch.setattr(runner.model_gateway, "call_omni_text", respond)
    # The pipeline must not accidentally reintroduce the non-streaming VL path.
    from shared import api_openai

    monkeypatch.setattr(api_openai, "call_openai_chat", lambda **kwargs: pytest.fail("unexpected VL request"))
    for name in ("plan_document", "write_document", "review_candidates", "review_pdf"):
        monkeypatch.setattr(runner.model_gateway, name, lambda *a, **kw: pytest.fail("unexpected auxiliary stage"))
    return calls


def _run(tmp_path, **arguments):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    result = runner.run_video2note(
        video_path=video, output_path=output, language="en", omni_model="test-omni", **arguments
    )
    return result, output


def _assert_pdf(result, output):
    assert result["status"] == "complete", result
    assert output.read_bytes().startswith(b"%PDF-")
    assert rendering.pdf_page_count(output) >= 1
    assert not list(output.parent.glob("omni-video2note-*"))


def test_two_omni_calls_produce_illustrated_pdf_without_model_review(tmp_path, monkeypatch):
    calls = _install_model_mocks(monkeypatch)
    result, output = _run(tmp_path, vl_model="ignored-vl", review_model="ignored-review")
    _assert_pdf(result, output)
    assert calls == [("understand_video", "test-omni"), ("generate_document", "test-omni")]
    assert result["images"] >= 1
    assert result["review"] is None
    assert result["audit"] is None
    assert {"prepare_video", "understanding", "note_generation", "screenshots", "render"} <= result["timings"].keys()


@pytest.mark.parametrize("malformed", [False, True])
def test_writing_failure_keeps_understood_evidence_and_delivers_pdf(tmp_path, monkeypatch, malformed):
    calls = _install_model_mocks(monkeypatch, writing_failure=not malformed, malformed=malformed)
    result, output = _run(tmp_path)
    _assert_pdf(result, output)
    assert len(calls) == 2  # No model-format-repair round.
    assert result["warnings"]
    if not HAS_TEXT_RENDERER:
        pytest.skip("weasyprint or reportlab is required to render extractable PDF text")
    pdfium = pytest.importorskip("pypdfium2")

    with pdfium.PdfDocument(str(output)) as pdf:
        content = " ".join(page.get_textpage().get_text_range() for page in pdf)
    assert "activate" in content.lower()
    assert '"steps"' not in content


def test_screenshot_failure_delivers_text_pdf(tmp_path, monkeypatch):
    _install_model_mocks(monkeypatch)

    def fail(*args):
        raise RuntimeError("frame decode failure")

    monkeypatch.setattr(runner, "select_timeline_frames", fail)
    result, output = _run(tmp_path)
    _assert_pdf(result, output)
    assert result["images"] == 0
    assert any("Screenshots unavailable" in warning for warning in result["warnings"])


def test_illustrated_render_failure_retries_locally_without_images(tmp_path, monkeypatch):
    _install_model_mocks(monkeypatch)
    real_render = runner.render_document
    selections_seen = []

    def render(draft, selections, *args, **kwargs):
        count = sum(len(step.choices) for step in selections.selections)
        selections_seen.append(count)
        if count:
            raise RuntimeError("image placement failure")
        return real_render(draft, selections, *args, **kwargs)

    monkeypatch.setattr(runner, "render_document", render)
    result, output = _run(tmp_path)
    _assert_pdf(result, output)
    assert selections_seen[0] > 0 and selections_seen[-1] == 0
    assert result["images"] == 0


def test_no_rendered_pdf_is_hard_failure(tmp_path, monkeypatch):
    _install_model_mocks(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("renderer produced no PDF")

    monkeypatch.setattr(runner, "render_document", fail)
    result, output = _run(tmp_path)
    assert result["status"] == "failed"
    assert "no PDF" in result["error"]
    assert not output.exists()


def test_all_understanding_requests_fail_without_fabricating_a_note(tmp_path, monkeypatch):
    _install_model_mocks(monkeypatch)

    def fail(*args, **kwargs):
        raise TimeoutError("service unavailable")

    monkeypatch.setattr(runner.model_gateway, "call_omni_text", fail)
    result, output = _run(tmp_path)
    assert result["status"] == "failed"
    assert not output.exists()
    assert not list(tmp_path.glob("omni-video2note-*"))


def test_failed_overwrite_preserves_previous_pdf(tmp_path, monkeypatch):
    _install_model_mocks(monkeypatch)
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"
    previous = b"existing PDF contents"
    output.write_bytes(previous)

    def fail(*args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(runner, "render_document", fail)
    result = runner.run_video2note(video_path=video, output_path=output, overwrite=True)
    assert result["status"] == "failed"
    assert output.read_bytes() == previous
