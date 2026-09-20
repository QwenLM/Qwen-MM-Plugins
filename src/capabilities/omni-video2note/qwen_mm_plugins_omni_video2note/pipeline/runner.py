"""Bounded Omni-only understanding, note generation, and local PDF rendering."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import model_gateway
from .artifacts import file_sha256
from .config import PipelineConfig
from .media import prepare_video_chunks, probe_video
from .quick_selection import select_timeline_frames
from .rendering import pdf_page_count, render_document
from .schemas import SelectionResult, StepSelection

PHASES = ["understanding", "note_generation", "local_render"]


def _without_images(plan: Any) -> SelectionResult:
    return SelectionResult(
        selections=[StepSelection(step_id=step.id) for step in plan.steps],
        tried=[],
    )


def _create_note(config: PipelineConfig) -> dict[str, Any]:
    started = time.monotonic()
    config.deadline = started + config.time_budget_seconds
    timings: dict[str, float] = {}
    probe = probe_video(config.video)
    config._source_duration = probe.duration
    if config.require_asr and not probe.has_audio:
        raise ValueError("require_asr was requested but the input video has no audio stream")
    input_hash = file_sha256(config.video)
    config.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="omni-video2note-", dir=config.output.parent) as directory:
        workdir = Path(directory)
        phase_start = time.monotonic()
        chunks = prepare_video_chunks(
            config.video,
            workdir / "chunks",
            chunk_seconds=config.profile.chunk_seconds,
            include_audio=not config.no_asr,
        )
        timings["prepare_video"] = round(time.monotonic() - phase_start, 3)
        phase_start = time.monotonic()
        understanding = model_gateway.understand_video(config, probe, chunks)
        timings["understanding"] = round(time.monotonic() - phase_start, 3)

        phase_start = time.monotonic()
        plan, draft = model_gateway.generate_document(config, understanding)
        if config.title:
            plan.title = draft.title = config.title
        draft.validate_against(plan)
        timings["note_generation"] = round(time.monotonic() - phase_start, 3)

        phase_start = time.monotonic()
        try:
            selections = select_timeline_frames(config, plan, probe, workdir)
        except Exception:
            config.warnings.append("Screenshots unavailable; the PDF contains the available text.")
            selections = _without_images(plan)
        timings["screenshots"] = round(time.monotonic() - phase_start, 3)

        phase_start = time.monotonic()
        render_options = dict(
            image_root=workdir,
            input_hash=input_hash,
            language=understanding.language if config.language == "auto" else config.language,
            font=config.font,
            bold_font=config.bold_font,
        )
        try:
            rendered = render_document(draft, selections, workdir / "document", **render_options)
        except Exception:
            if not any(item.choices for item in selections.selections):
                raise
            config.warnings.append("Illustrated rendering failed; generated a text-only PDF instead.")
            selections = _without_images(plan)
            rendered = render_document(draft, selections, workdir / "document", **render_options)
        if not rendered.pdf_path.is_file() or pdf_page_count(rendered.pdf_path) < 1:
            raise RuntimeError("renderer produced no readable PDF")
        timings["render"] = round(time.monotonic() - phase_start, 3)

        if file_sha256(config.video) != input_hash:
            raise RuntimeError("input video changed during processing; retry with a stable source file")
        if config.output.exists() and not config.overwrite:
            raise FileExistsError("output PDF already exists; use overwrite=true to replace it")
        os.replace(rendered.pdf_path, config.output)

    return {
        "exit_code": 0,
        "status": "complete",
        "output_path": str(config.output),
        "title": draft.title,
        "steps": len(draft.steps),
        "images": sum(len(item.choices) for item in selections.selections),
        "duration_seconds": round(probe.duration, 3),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "timings": timings,
        "api_calls": config.api_calls,
        "audit": None,
        "review": None,
        "warnings": config.warnings,
    }


def run_video2note(**arguments: Any) -> dict[str, Any]:
    """Generate a PDF; preserve usable content after optional-stage API failures."""
    config = None
    started = time.monotonic()
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
        # The transport exposes credential-free errors; also redact any configured key from
        # unexpected failures before returning the MCP result.
        message = str(exc)
        if config is not None and config._api_key and config._api_key != "EMPTY":
            message = message.replace(config._api_key, "[redacted]")
        return {
            "exit_code": 1,
            "status": "failed",
            "output_path": str(arguments.get("output_path", "")),
            "error": message,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "warnings": config.warnings if config else [],
            "api_calls": config.api_calls if config else [],
        }


run = run_video2note
