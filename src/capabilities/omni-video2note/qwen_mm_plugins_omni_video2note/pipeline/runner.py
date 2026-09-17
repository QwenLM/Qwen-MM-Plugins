"""Resumable four-phase Omni Video2Note pipeline orchestration."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import model_gateway
from .audit import audit_pdf, best_candidate, normalize_repair_actions, passes_review_gate
from .config import PipelineConfig
from .frame_selection import FrameSelector, SelectionConfig
from .media import MediaChunk, prepare_video_chunks, probe_video
from .rendering import build_document_html, render_document
from .schemas import (
    DocumentDraft,
    DocumentPlan,
    FinalReport,
    FrameCandidate,
    IterationReport,
    RepairAction,
    ReviewReport,
    SelectionResult,
    VideoUnderstanding,
)
from .state import PipelineState, atomic_write_json, load_json, offline_status

_PHASE1 = "phase1_understanding"
_PHASE2 = "phase2_planning"
_PHASE3 = "phase3_writing_selection"
_PHASE4 = "phase4_render_review_repair"
_STATUS_ORDER = ["new", "understanding", "planning", "writing_selection", "render_review_repair"]
# Argument, schema, and workdir-ownership violations are terminal: retrying them changes nothing.
# Every other failure (media tooling, model calls, I/O) is an interruption, so the job stays
# resumable and its manifest keeps the phase it reached instead of becoming terminally failed.
_HARD_FAILURES = (ValueError, TypeError, AssertionError, KeyError, FileExistsError, NotADirectoryError)


def _write_schema(path: Path, value: Any) -> None:
    atomic_write_json(path, value.to_dict() if hasattr(value, "to_dict") else value)


def _advance(state: PipelineState, target: str) -> None:
    if state.status == target:
        return
    if state.status not in _STATUS_ORDER or target not in _STATUS_ORDER:
        raise RuntimeError(f"cannot advance terminal state {state.status!r} to {target!r}")
    current = _STATUS_ORDER.index(state.status)
    destination = _STATUS_ORDER.index(target)
    if destination < current:
        return
    for status in _STATUS_ORDER[current + 1 : destination + 1]:
        state.transition(status)


def _checkpoint_artifact(state: PipelineState, phase: str, key: str) -> Path:
    record = state.manifest["checkpoints"].get(phase, {})
    relative = record.get("artifacts", {}).get(key)
    if not isinstance(relative, str):
        raise RuntimeError(f"checkpoint {phase} does not contain artifact {key}")
    return state.artifact_path(relative)


def _load_chunks(path: Path) -> list[MediaChunk]:
    raw = load_json(path)
    if not isinstance(raw, list):
        raise TypeError("chunks artifact must be an array")
    chunks = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("chunk artifact entries must be objects")
        chunks.append(
            MediaChunk(
                path=Path(item["path"]).expanduser().resolve(),
                source_start=float(item["source_start"]),
                source_end=float(item["source_end"]),
                delivery=dict(item["delivery"]),
            )
        )
    return chunks


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _is_valid_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 16:
        return False
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            return False
        try:
            handle.seek(-4096, os.SEEK_END)
        except OSError:
            handle.seek(0)
        return b"%%EOF" in handle.read()


def _result(report: FinalReport, config: PipelineConfig) -> dict[str, Any]:
    return {
        "exit_code": 0 if report.status == "pass" else 2 if report.status == "best_effort" else 1,
        "status": report.status,
        "passed": report.passed,
        "output_path": str(config.output),
        "workdir": str(config.workdir),
        "best_iteration": report.best_iteration,
        "best_score": report.best_score,
        "iterations": len(report.iterations),
        "error": report.error,
        "resumable": False,
    }


def _review_unavailable(error: Exception) -> ReviewReport:
    return ReviewReport(
        verdict="best_effort",
        overall=0.0,
        scores={
            "accuracy": 0.0,
            "completeness": 0.0,
            "clarity": 0.0,
            "visual_quality": 0.0,
        },
        issues=[],
        summary=f"model PDF review unavailable: {error}",
        available=False,
    )


def _phase1(config: PipelineConfig, state: PipelineState) -> tuple[Any, list[FrameCandidate], list[MediaChunk], VideoUnderstanding]:
    if state.is_checkpoint_complete(_PHASE1):
        probe = probe_video(config.video)
        if config.require_asr and not probe.has_audio:
            raise RuntimeError("require_asr was requested but the input video has no audio stream")
        probe_saved = load_json(_checkpoint_artifact(state, _PHASE1, "probe"))
        if probe.to_dict() != probe_saved:
            raise RuntimeError("saved probe metadata no longer matches the input video")
        coarse = [FrameCandidate.parse(item) for item in load_json(_checkpoint_artifact(state, _PHASE1, "coarse"))]
        chunks = _load_chunks(_checkpoint_artifact(state, _PHASE1, "chunks"))
        understanding = VideoUnderstanding.parse(load_json(_checkpoint_artifact(state, _PHASE1, "understanding")))
        _advance(state, "planning")
        return probe, coarse, chunks, understanding
    _advance(state, "understanding")
    state.checkpoint(_PHASE1, "running")
    phase_dir = state.workdir / "phase1"
    phase_dir.mkdir(parents=True, exist_ok=True)
    probe = probe_video(config.video)
    if config.require_asr and not probe.has_audio:
        raise RuntimeError("require_asr was requested but the input video has no audio stream")
    probe_path = phase_dir / "probe.json"
    _write_schema(probe_path, probe)
    selector = FrameSelector(
        config.video,
        state.workdir,
        duration=probe.duration,
        config=SelectionConfig.from_profile(config.profile),
    )
    coarse = selector.prepare_coarse()
    coarse_path = phase_dir / "coarse.json"
    atomic_write_json(coarse_path, [item.to_dict() for item in coarse])
    chunks = prepare_video_chunks(
        config.video,
        phase_dir / "chunks",
        chunk_seconds=config.profile.chunk_seconds,
        include_audio=not config.no_asr,
    )
    chunks_path = phase_dir / "chunks.json"
    atomic_write_json(chunks_path, [item.to_dict() for item in chunks])
    understanding = model_gateway.understand_video(config, probe, chunks)
    understanding_path = phase_dir / "understanding.json"
    _write_schema(understanding_path, understanding)
    state.checkpoint(
        _PHASE1,
        "complete",
        artifacts={
            "probe": state.relative_artifact(probe_path),
            "coarse": state.relative_artifact(coarse_path),
            "coarse_frames": "frames/r0",
            "chunks": state.relative_artifact(chunks_path),
            "chunk_media": state.relative_artifact(phase_dir / "chunks"),
            "understanding": state.relative_artifact(understanding_path),
        },
        data={"chunks": len(chunks), "coarse_frames": len(coarse)},
    )
    _advance(state, "planning")
    return probe, coarse, chunks, understanding


def _validate_plan_duration(plan: DocumentPlan, duration: float) -> None:
    for step in plan.steps:
        if step.start >= duration or step.end > duration + 0.1:
            raise ValueError(
                f"plan step {step.id} range {step.start:.3f}-{step.end:.3f}s exceeds video duration {duration:.3f}s"
            )


def _phase2(
    config: PipelineConfig,
    state: PipelineState,
    understanding: VideoUnderstanding,
    duration: float,
) -> DocumentPlan:
    if state.is_checkpoint_complete(_PHASE2):
        plan = DocumentPlan.parse(load_json(_checkpoint_artifact(state, _PHASE2, "plan")))
        _validate_plan_duration(plan, duration)
        _advance(state, "writing_selection")
        return plan
    _advance(state, "planning")
    state.checkpoint(_PHASE2, "running")
    plan = model_gateway.plan_document(config, understanding)
    _validate_plan_duration(plan, duration)
    plan_path = state.workdir / "phase2" / "plan.json"
    _write_schema(plan_path, plan)
    state.checkpoint(
        _PHASE2,
        "complete",
        artifacts={"plan": state.relative_artifact(plan_path)},
        data={"steps": len(plan.steps)},
    )
    _advance(state, "writing_selection")
    return plan


def _select_frames(
    config: PipelineConfig,
    state: PipelineState,
    probe: Any,
    plan: DocumentPlan,
    coarse: list[FrameCandidate],
) -> SelectionResult:
    selector = FrameSelector(
        config.video,
        state.workdir,
        duration=probe.duration,
        config=SelectionConfig.from_profile(config.profile),
    )
    if coarse:
        selector.restore_coarse(coarse)
    return selector.select_steps(plan.steps, lambda requests: model_gateway.review_candidates(config, requests))


def _phase3(
    config: PipelineConfig,
    state: PipelineState,
    probe: Any,
    understanding: VideoUnderstanding,
    plan: DocumentPlan,
    coarse: list[FrameCandidate],
) -> tuple[DocumentDraft, SelectionResult]:
    if state.is_checkpoint_complete(_PHASE3):
        draft = DocumentDraft.parse(load_json(_checkpoint_artifact(state, _PHASE3, "draft")))
        draft.validate_against(plan)
        selections = SelectionResult.parse(load_json(_checkpoint_artifact(state, _PHASE3, "selections")))
        _advance(state, "render_review_repair")
        return draft, selections
    _advance(state, "writing_selection")
    phase_dir = state.workdir / "phase3"
    draft_path = phase_dir / "draft.json"
    checkpoint = state.manifest["checkpoints"].get(_PHASE3, {})
    saved_draft = checkpoint.get("artifacts", {}).get("draft")
    if checkpoint.get("status") == "running" and isinstance(saved_draft, str) and state.artifact_path(saved_draft).is_file():
        draft = DocumentDraft.parse(load_json(state.artifact_path(saved_draft)))
    else:
        state.checkpoint(_PHASE3, "running")
        draft = model_gateway.write_document(config, plan, understanding)
        draft.validate_against(plan)
        _write_schema(draft_path, draft)
        state.checkpoint(
            _PHASE3,
            "running",
            artifacts={"draft": state.relative_artifact(draft_path)},
            data={"draft_complete": True},
        )
    draft.validate_against(plan)
    selections = _select_frames(config, state, probe, plan, coarse)
    selections_path = phase_dir / "selections.json"
    _write_schema(selections_path, selections)
    built = build_document_html(
        draft,
        selections,
        phase_dir,
        image_root=state.workdir,
        input_hash=state.manifest["input"]["sha256"],
        language=config.language,
        font=config.font,
        bold_font=config.bold_font,
    )
    state.checkpoint(
        _PHASE3,
        "complete",
        artifacts={
            "draft": state.relative_artifact(draft_path),
            "selections": state.relative_artifact(selections_path),
            "html": state.relative_artifact(built.html_path),
            "layout": state.relative_artifact(built.layout_path),
        },
        data={"selected_images": sum(len(item.choices) for item in selections.selections)},
    )
    _advance(state, "render_review_repair")
    return draft, selections


def _merge_selections(current: SelectionResult, replacement: SelectionResult) -> SelectionResult:
    updates = {item.step_id: item for item in replacement.selections}
    merged = [updates.get(item.step_id, item) for item in current.selections]
    known = {item.step_id for item in merged}
    merged.extend(item for item in replacement.selections if item.step_id not in known)
    result = SelectionResult(selections=sorted(merged, key=lambda item: item.step_id), tried=[*current.tried, *replacement.tried])
    result.validate()
    return result


def _apply_repairs(
    config: PipelineConfig,
    state: PipelineState,
    probe: Any,
    coarse: list[FrameCandidate],
    understanding: VideoUnderstanding,
    plan: DocumentPlan,
    draft: DocumentDraft,
    selections: SelectionResult,
    repairs: list[RepairAction],
    layout_options: dict[str, Any],
) -> tuple[DocumentPlan, DocumentDraft, SelectionResult, dict[str, Any]]:
    replan = [item for item in repairs if item.type == "replan_document"]
    rewrite = [item for item in repairs if item.type == "rewrite_text"]
    reselect = [item for item in repairs if item.type == "reselect_image"]
    adjust = [item for item in repairs if item.type == "adjust_layout"]
    if replan:
        plan = model_gateway.plan_document(config, understanding, previous_plan=plan, repairs=replan)
        _validate_plan_duration(plan, probe.duration)
        draft = model_gateway.write_document(config, plan, understanding, current_draft=draft, repairs=replan)
        selections = _select_frames(config, state, probe, plan, coarse)
    elif rewrite:
        draft = model_gateway.write_document(config, plan, understanding, current_draft=draft, repairs=rewrite)
    if reselect and not replan:
        step_ids = {item.step_id for item in reselect if item.step_id is not None}
        affected = [step for step in plan.steps if step.id in step_ids]
        if affected:
            selector = FrameSelector(
                config.video,
                state.workdir,
                duration=probe.duration,
                config=SelectionConfig.from_profile(config.profile),
            )
            if coarse:
                selector.restore_coarse(coarse)
            replacement = selector.select_steps(
                affected,
                lambda requests: model_gateway.review_candidates(config, requests),
            )
            selections = _merge_selections(selections, replacement)
    for action in adjust:
        instruction = action.instruction.casefold()
        if any(word in instruction for word in ("overflow", "crowd", "拥挤", "溢出", "too large")):
            layout_options["font_scale"] = max(0.75, float(layout_options.get("font_scale", 1.0)) - 0.08)
            layout_options["image_max_height"] = max(220, int(layout_options.get("image_max_height", 420)) - 40)
        elif any(word in instruction for word in ("small", "tiny", "太小")):
            layout_options["font_scale"] = min(1.3, float(layout_options.get("font_scale", 1.0)) + 0.08)
        else:
            layout_options["page_break_steps"] = True
    draft.validate_against(plan)
    return plan, draft, selections, layout_options


def _load_iteration_reports(path: Path) -> list[IterationReport]:
    if not path.is_file():
        return []
    raw = load_json(path)
    if not isinstance(raw, list):
        raise TypeError("iteration history must be an array")
    reports = [IterationReport.parse(item) for item in raw]
    return [item for item in reports if (path.parent.parent / item.candidate_pdf).is_file()]


def _phase4(
    config: PipelineConfig,
    state: PipelineState,
    probe: Any,
    coarse: list[FrameCandidate],
    understanding: VideoUnderstanding,
    plan: DocumentPlan,
    draft: DocumentDraft,
    selections: SelectionResult,
) -> FinalReport:
    _advance(state, "render_review_repair")
    if state.is_checkpoint_complete(_PHASE4):
        completed = FinalReport.parse(load_json(_checkpoint_artifact(state, _PHASE4, "final_report")))
        if completed.final_pdf is None:
            raise RuntimeError("completed render checkpoint has no final PDF")
        final_pdf = state.artifact_path(completed.final_pdf)
        if not _is_valid_pdf(final_pdf):
            raise RuntimeError("completed render checkpoint has an invalid final PDF")
        _atomic_copy(final_pdf, config.output)
        state.transition(completed.status)
        return completed
    phase_dir = state.workdir / "phase4"
    phase_dir.mkdir(parents=True, exist_ok=True)
    history_path = phase_dir / "iterations.json"
    current_plan_path = phase_dir / "current_plan.json"
    current_draft_path = phase_dir / "current_draft.json"
    current_selections_path = phase_dir / "current_selections.json"
    current_layout_path = phase_dir / "current_layout.json"
    phase_record = state.manifest["checkpoints"].get(_PHASE4, {})
    committed_count = int(phase_record.get("data", {}).get("completed_iterations", 0))
    reports = _load_iteration_reports(history_path)[:committed_count]
    if history_path.is_file():
        atomic_write_json(history_path, [item.to_dict() for item in reports])
    layout_options: dict[str, Any] = {}
    if reports and all(path.is_file() for path in (current_plan_path, current_draft_path, current_selections_path)):
        plan = DocumentPlan.parse(load_json(current_plan_path))
        draft = DocumentDraft.parse(load_json(current_draft_path))
        selections = SelectionResult.parse(load_json(current_selections_path))
        if current_layout_path.is_file():
            raw_layout = load_json(current_layout_path)
            if isinstance(raw_layout, dict):
                layout_options = raw_layout
    start_iteration = len(reports) + 1
    state.checkpoint(
        _PHASE4,
        "running",
        artifacts={"history": state.relative_artifact(history_path)} if history_path.exists() else {},
        data={"completed_iterations": len(reports)},
    )
    for iteration in range(start_iteration, int(config.max_iterations) + 1):
        iteration_dir = phase_dir / f"iteration-{iteration:03d}"
        rendered = render_document(
            draft,
            selections,
            iteration_dir,
            image_root=state.workdir,
            input_hash=state.manifest["input"]["sha256"],
            language=config.language,
            font=config.font,
            bold_font=config.bold_font,
            layout_options=layout_options,
            pdf_name="candidate.pdf",
        )
        _write_schema(iteration_dir / "plan.json", plan)
        _write_schema(iteration_dir / "draft.json", draft)
        _write_schema(iteration_dir / "selections.json", selections)
        audit = audit_pdf(
            rendered.pdf_path,
            rendered.html_path,
            rendered.layout_path,
            selections,
            image_root=state.workdir,
            input_path=config.video,
            expected_input_hash=state.manifest["input"]["sha256"],
            plan=plan,
            preview_dir=iteration_dir / "audit-pages",
        )
        _write_schema(iteration_dir / "audit.json", audit)
        try:
            review = model_gateway.review_pdf(
                config,
                rendered.pdf_path,
                audit,
                understanding=understanding,
                plan=plan,
                draft=draft,
                preview_dir=iteration_dir / "audit-pages",
            )
        except Exception as exc:  # model review failure still preserves a valid PDF
            review = _review_unavailable(exc)
        _write_schema(iteration_dir / "review.json", review)
        complete = passes_review_gate(audit, review)
        repairs: list[RepairAction] = []
        if not complete and iteration < int(config.max_iterations) and review.available:
            try:
                proposed = model_gateway.propose_repairs(config, plan, draft, audit, review)
                repairs = normalize_repair_actions(
                    proposed,
                    valid_step_ids={step.id for step in plan.steps},
                    valid_target_ids={target.id for step in plan.steps for target in step.visual_targets},
                )
            except Exception:
                repairs = []
            if not repairs:
                repairs = [RepairAction(type="adjust_layout", instruction="reduce crowding and isolate steps")]
        _write_schema(iteration_dir / "repairs.json", [item.to_dict() for item in repairs])
        report = IterationReport(
            iteration=iteration,
            candidate_pdf=state.relative_artifact(rendered.pdf_path),
            audit=audit,
            review=review,
            repairs=repairs,
            complete=complete,
            stop_reason="quality gate passed" if complete else "review unavailable" if not review.available else "below quality gate",
        )
        report.validate()
        _write_schema(iteration_dir / "iteration.json", report)
        reports.append(report)
        atomic_write_json(history_path, [item.to_dict() for item in reports])
        if repairs:
            plan, draft, selections, layout_options = _apply_repairs(
                config,
                state,
                probe,
                coarse,
                understanding,
                plan,
                draft,
                selections,
                repairs,
                layout_options,
            )
        _write_schema(current_plan_path, plan)
        _write_schema(current_draft_path, draft)
        _write_schema(current_selections_path, selections)
        atomic_write_json(current_layout_path, layout_options)
        state.checkpoint(
            _PHASE4,
            "running",
            artifacts={
                "history": state.relative_artifact(history_path),
                "current_plan": state.relative_artifact(current_plan_path),
                "current_draft": state.relative_artifact(current_draft_path),
                "current_selections": state.relative_artifact(current_selections_path),
                "current_layout": state.relative_artifact(current_layout_path),
            },
            data={"completed_iterations": len(reports)},
        )
        if complete or not review.available:
            break
    best = best_candidate(reports)
    if best is None:
        raise RuntimeError("render phase produced no candidate")
    candidate = state.artifact_path(best.candidate_pdf)
    if not _is_valid_pdf(candidate):
        raise RuntimeError("render phase produced no valid PDF")
    best_path = phase_dir / "best.pdf"
    _atomic_copy(candidate, best_path)
    passed = passes_review_gate(best.audit, best.review)
    status = "pass" if passed else "best_effort"
    final_report = FinalReport(
        status=status,
        passed=passed,
        final_pdf=state.relative_artifact(best_path),
        best_iteration=best.iteration,
        best_score=best.review.overall,
        iterations=reports,
        error="" if passed else best.review.summary or "best PDF is below the quality gate",
    )
    final_path = phase_dir / "final_report.json"
    _write_schema(final_path, final_report)
    _atomic_copy(best_path, config.output)
    state.checkpoint(
        _PHASE4,
        "complete",
        artifacts={
            "history": state.relative_artifact(history_path),
            "best_pdf": state.relative_artifact(best_path),
            "final_report": state.relative_artifact(final_path),
        },
        data={"best_iteration": best.iteration, "passed": passed},
    )
    state.transition(status)
    return final_report


def _audited_candidates(state: PipelineState) -> list[IterationReport]:
    """Committed iteration reports whose candidate PDF is still valid and bound to this input.

    Only evidence the pipeline itself committed counts: the manifest-counted history plus the
    per-iteration reports, each of which is written after both its audit and its model review.
    """
    phase_dir = state.workdir / "phase4"
    record = state.manifest["checkpoints"].get(_PHASE4, {})
    committed = int(record.get("data", {}).get("completed_iterations", 0))
    by_iteration: dict[int, IterationReport] = {}
    for path in sorted(phase_dir.glob("iteration-*/iteration.json")):
        try:
            report = IterationReport.parse(load_json(path))
        except (OSError, TypeError, ValueError):
            continue
        by_iteration[report.iteration] = report
    try:
        for report in _load_iteration_reports(phase_dir / "iterations.json")[:committed]:
            by_iteration[report.iteration] = report
    except (OSError, TypeError, ValueError):
        pass
    audited: list[IterationReport] = []
    for iteration in sorted(by_iteration):
        report = by_iteration[iteration]
        if not report.audit.input_hash_matches:
            continue
        try:
            candidate = state.artifact_path(report.candidate_pdf)
        except ValueError:
            continue
        if _is_valid_pdf(candidate):
            audited.append(report)
    return audited


def _recover_valid_pdf(config: PipelineConfig, state: PipelineState, error: Exception) -> dict[str, Any] | None:
    """Publish the best already-audited candidate, or nothing when no audited candidate exists."""
    if state.status != "render_review_repair":
        return None
    reports = _audited_candidates(state)
    best = best_candidate(reports)
    if best is None:
        return None
    best_path = state.workdir / "phase4" / "best.pdf"
    _atomic_copy(state.artifact_path(best.candidate_pdf), best_path)
    _atomic_copy(best_path, config.output)
    report = FinalReport(
        status="best_effort",
        passed=False,
        final_pdf=state.relative_artifact(best_path),
        best_iteration=best.iteration,
        best_score=best.review.overall,
        iterations=reports,
        error=str(error),
    )
    final_path = state.workdir / "phase4" / "final_report.json"
    _write_schema(final_path, report)
    state.checkpoint(
        _PHASE4,
        "complete",
        artifacts={"best_pdf": state.relative_artifact(best_path), "final_report": state.relative_artifact(final_path)},
        data={"recovered": True, "passed": False, "best_iteration": best.iteration},
        error=str(error),
    )
    state.transition("best_effort")
    return _result(report, config)


def run_video2note(**arguments: Any) -> dict[str, Any]:
    """Run or resume all four phases and return the stable JSON result contract."""
    state: PipelineState | None = None
    config: PipelineConfig | None = None
    try:
        config = PipelineConfig(**arguments)
        if config.dry_run:
            return {
                "exit_code": 0,
                "status": "dry_run",
                "passed": False,
                "output_path": str(config.output),
                "workdir": str(config.workdir),
                "best_iteration": None,
                "best_score": 0.0,
                "iterations": 0,
                "error": "",
                "resumable": False,
                "config": config.to_dict(),
                "phases": [_PHASE1, _PHASE2, _PHASE3, _PHASE4],
            }
        state = (
            PipelineState.resume(config.workdir, config.video, config.fingerprint())
            if config.resume
            else PipelineState.create(config.workdir, config.video, config.fingerprint(), overwrite=config.overwrite)
        )
        if not config.resume and config.output.exists():
            # The new job owns this path: drop the previous PDF now so a failure cannot leave the
            # caller with a stale document that looks like this run's result.
            config.output.unlink()
        if state.status in {"pass", "best_effort"}:
            final_path = state.workdir / "phase4" / "final_report.json"
            report = FinalReport.parse(load_json(final_path))
            if report.final_pdf is None or not _is_valid_pdf(state.artifact_path(report.final_pdf)):
                raise RuntimeError("terminal job is missing its valid final PDF")
            _atomic_copy(state.artifact_path(report.final_pdf), config.output)
            return _result(report, config)
        if state.status == "failed":
            raise RuntimeError("failed terminal job cannot be resumed; start again with overwrite=true")
        probe, coarse, _chunks, understanding = _phase1(config, state)
        plan = _phase2(config, state, understanding, probe.duration)
        draft, selections = _phase3(config, state, probe, understanding, plan, coarse)
        report = _phase4(config, state, probe, coarse, understanding, plan, draft, selections)
        return _result(report, config)
    except Exception as exc:
        if config is not None and state is not None:
            try:
                recovered = _recover_valid_pdf(config, state, exc)
            except Exception:
                recovered = None
            if recovered is not None:
                return recovered
            try:
                if isinstance(exc, _HARD_FAILURES) and state.status not in {"pass", "best_effort", "failed"}:
                    state.transition("failed", error=str(exc))
            except Exception:
                pass
        return {
            "exit_code": 1,
            "status": "failed",
            "passed": False,
            "output_path": str(config.output) if config else str(arguments.get("output_path", "")),
            "workdir": str(config.workdir) if config else str(arguments.get("workdir") or ""),
            "best_iteration": None,
            "best_score": 0.0,
            "iterations": 0,
            "error": str(exc),
            "resumable": bool(state and state.status not in {"pass", "best_effort", "failed"}),
        }


def get_status(*, workdir: str | Path | None = None, output_path: str | Path | None = None) -> dict[str, Any]:
    """Read status using only local files; derive the default workdir from output_path when omitted."""
    if workdir is None:
        if output_path is None:
            return {
                "exit_code": 1,
                "status": "failed",
                "passed": False,
                "workdir": "",
                "resumable": False,
                "final_pdf": None,
                "best_iteration": None,
                "best_score": 0.0,
                "iterations": 0,
                "error": "workdir or output_path is required",
            }
        output = Path(output_path).expanduser().resolve()
        workdir = output.with_suffix(output.suffix + ".work")
    return offline_status(workdir)


run = run_video2note
