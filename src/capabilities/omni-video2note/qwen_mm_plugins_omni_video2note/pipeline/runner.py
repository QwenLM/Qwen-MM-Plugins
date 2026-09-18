"""Synchronous four-stage Omni Video2Note pipeline."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from . import model_gateway
from .artifacts import file_sha256
from .audit import audit_pdf, passes_review_gate
from .config import PipelineConfig
from .frame_selection import FrameSelector, SelectionConfig
from .media import prepare_video_chunks, probe_video
from .rendering import pdf_page_count, render_document

PHASES = ["understanding", "planning", "writing_selection", "render_review"]


def _create_note(config: PipelineConfig) -> dict[str, Any]:
    probe = probe_video(config.video)
    if config.require_asr and not probe.has_audio:
        raise ValueError("require_asr was requested but the input video has no audio stream")
    input_hash = file_sha256(config.video)
    config.output.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    # Every call owns its temporary artifacts. A failed call can simply be retried.
    with tempfile.TemporaryDirectory(prefix="omni-video2note-", dir=config.output.parent) as directory:
        workdir = Path(directory)
        selector = FrameSelector(
            config.video,
            workdir,
            duration=probe.duration,
            config=SelectionConfig.from_profile(config.profile),
        )
        selector.prepare_coarse()
        chunks = prepare_video_chunks(
            config.video,
            workdir / "chunks",
            chunk_seconds=config.profile.chunk_seconds,
            include_audio=not config.no_asr,
        )
        understanding = model_gateway.understand_video(config, probe, chunks)
        plan = model_gateway.plan_document(config, understanding)
        if config.title:
            plan.title = config.title
        for step in plan.steps:
            if step.start >= probe.duration or step.end > probe.duration + 0.1:
                raise ValueError(f"plan step {step.id} exceeds the video duration")

        draft = model_gateway.write_document(config, plan, understanding)
        if config.title:
            draft.title = config.title
        draft.validate_against(plan)
        selections = selector.select_steps(
            plan.steps, lambda requests: model_gateway.review_candidates(config, requests)
        )
        for selection in selections.selections:
            for target, reason in selection.misses.items():
                warnings.append(f"Step {selection.step_id}, image {target}: {reason}")
        rendered = render_document(
            draft,
            selections,
            workdir / "document",
            image_root=workdir,
            input_hash=input_hash,
            language=understanding.language if config.language == "auto" else config.language,
            font=config.font,
            bold_font=config.bold_font,
        )
        if not rendered.pdf_path.is_file() or pdf_page_count(rendered.pdf_path) < 1:
            raise RuntimeError("renderer produced no readable PDF")

        # Review is feedback for the caller, without a state machine or automatic repair loop.
        audit = None
        review = None
        try:
            audit = audit_pdf(
                rendered.pdf_path,
                rendered.html_path,
                rendered.layout_path,
                selections,
                image_root=workdir,
                input_path=config.video,
                expected_input_hash=input_hash,
                plan=plan,
                preview_dir=workdir / "review-pages",
            )
        except Exception as exc:
            warnings.append(f"PDF checks unavailable: {exc}")
        if audit is not None:
            if not audit.passed:
                warnings.append("PDF checks reported issues; see audit for details.")
            try:
                review = model_gateway.review_pdf(
                    config,
                    rendered.pdf_path,
                    audit,
                    understanding=understanding,
                    plan=plan,
                    draft=draft,
                    preview_dir=workdir / "review-pages",
                )
                if not passes_review_gate(audit, review):
                    warnings.append(review.summary or "PDF review suggests improvements; see review for details.")
            except Exception as exc:
                warnings.append(f"PDF review unavailable: {exc}")

        if file_sha256(config.video) != input_hash:
            raise RuntimeError("input video changed during processing; retry with a stable source file")
        if config.output.exists() and not config.overwrite:
            raise FileExistsError("output PDF already exists; use overwrite=true to replace it")
        # Publish only the finished PDF; an earlier output survives a failed replacement.
        os.replace(rendered.pdf_path, config.output)

    return {
        "exit_code": 0,
        "status": "complete",
        "output_path": str(config.output),
        "title": draft.title,
        "steps": len(draft.steps),
        "images": sum(len(item.choices) for item in selections.selections),
        "duration_seconds": round(probe.duration, 3),
        "audit": audit.to_dict() if audit is not None else None,
        "review": review.to_dict() if review is not None else None,
        "warnings": warnings,
    }


def run_video2note(**arguments: Any) -> dict[str, Any]:
    """Generate one PDF synchronously, or return the error for the caller to handle."""
    try:
        config = PipelineConfig(**arguments)
        if config.dry_run:
            return {
                "exit_code": 0,
                "status": "dry_run",
                "output_path": str(config.output),
                "config": config.to_dict(),
                "phases": PHASES,
            }
        return _create_note(config)
    except Exception as exc:
        return {
            "exit_code": 1,
            "status": "failed",
            "output_path": str(arguments.get("output_path", "")),
            "error": str(exc),
        }


run = run_video2note
