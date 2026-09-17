"""Deterministic PDF audits, review gates, candidate ranking, and repair normalization."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .image_quality import are_near_duplicates, evaluate_image
from .rendering import rasterize_pdf
from .schemas import (
    AuditReport,
    DocumentPlan,
    IterationReport,
    RepairAction,
    ReviewReport,
    SelectionResult,
    StepSelection,
)
from .state import file_sha256

REVIEW_DIMENSIONS = ("accuracy", "completeness", "clarity", "visual_quality")
_PLACEHOLDER = re.compile(
    r"\{\{\s*(?:frame|image|placeholder)(?:[_:\s-]*(\d+))?[^}]*\}\}"
    r"|data-placeholder\s*=|class\s*=\s*['\"][^'\"]*placeholder",
    re.IGNORECASE,
)


def _normalize_selections(
    value: SelectionResult | list[StepSelection] | list[dict[str, Any]] | dict[str, Any],
) -> list[StepSelection]:
    if isinstance(value, SelectionResult):
        return value.selections
    if isinstance(value, dict):
        value = value.get("selections", value.get("steps", []))
    if not isinstance(value, list):
        raise TypeError("selections must be a list or SelectionResult")
    return [item if isinstance(item, StepSelection) else StepSelection.parse(item) for item in value]


def _required_steps(plan: DocumentPlan | dict[str, Any] | None, layout: dict[str, Any]) -> list[int]:
    if plan is not None:
        normalized = plan if isinstance(plan, DocumentPlan) else DocumentPlan.parse(plan)
        return [step.id for step in normalized.steps if step.image_required]
    return [int(step["id"]) for step in layout.get("steps", [])]


def _page_diagnostics(previews: list[Path]) -> tuple[list[int], list[str]]:
    from PIL import Image, ImageChops

    blank_pages: list[int] = []
    edge_warnings: list[str] = []
    for page_number, path in enumerate(previews, 1):
        with Image.open(path) as opened:
            grayscale = opened.convert("L")
            width, height = grayscale.size
            white = Image.new("L", grayscale.size, 255)
            difference = ImageChops.difference(grayscale, white)
            bbox = difference.point(lambda value: 255 if value > 12 else 0).getbbox()
            if bbox is None:
                blank_pages.append(page_number)
                continue
            pixels = list(grayscale.resize((160, max(1, round(160 * height / width)))).getdata())
            ink_ratio = sum(value < 245 for value in pixels) / max(1, len(pixels))
            if ink_ratio < 0.002:
                blank_pages.append(page_number)
            left, top, right, bottom = bbox
            margin = max(4, round(min(width, height) * 0.008))
            if left <= margin or top <= margin or right >= width - margin or bottom >= height - margin:
                edge_warnings.append(f"page {page_number} content is close to the page edge")
    return blank_pages, edge_warnings


def audit_pdf(
    pdf_path: str | Path,
    html_path: str | Path,
    layout_path: str | Path,
    selections: SelectionResult | list[StepSelection] | list[dict[str, Any]] | dict[str, Any],
    *,
    image_root: str | Path,
    input_path: str | Path | None = None,
    expected_input_hash: str = "",
    plan: DocumentPlan | dict[str, Any] | None = None,
    preview_dir: str | Path | None = None,
) -> AuditReport:
    """Audit PDF integrity, pages, selected images, coverage, duplicates, and input identity."""
    pdf = Path(pdf_path).expanduser().resolve()
    html_file = Path(html_path).expanduser().resolve()
    layout_file = Path(layout_path).expanduser().resolve()
    root = Path(image_root).expanduser().resolve()
    normalized = _normalize_selections(selections)
    raw_pdf = pdf.read_bytes() if pdf.is_file() else b""
    valid_signature = raw_pdf.startswith(b"%PDF-") and b"%%EOF" in raw_pdf[-4096:]
    try:
        layout = json.loads(layout_file.read_text(encoding="utf-8"))
        if not isinstance(layout, dict):
            raise TypeError("layout root is not an object")
    except Exception as exc:
        layout = {}
        layout_error = str(exc)
    else:
        layout_error = ""
    try:
        html_text = html_file.read_text(encoding="utf-8")
    except Exception:
        html_text = ""
    placeholder_matches = list(_PLACEHOLDER.finditer(html_text))
    placeholder_ids = sorted({int(match.group(1)) for match in placeholder_matches if match.group(1)})
    unresolved = bool(placeholder_matches)

    expected = expected_input_hash or str(layout.get("input_sha256") or "")
    if input_path is None:
        actual = expected
    elif Path(input_path).is_file():
        actual = file_sha256(input_path)
    else:
        actual = ""
    input_hash_matches = bool(expected) and actual == expected and layout.get("input_sha256") == expected

    required = _required_steps(plan, layout)
    selected_primary = {
        selection.step_id
        for selection in normalized
        if any(choice.role == "primary" for choice in selection.choices)
    }
    missing_required = sorted(set(required) - selected_primary)
    coverage = len(set(required) & selected_primary) / len(required) if required else 1.0

    layout_images: dict[tuple[int, str], dict[str, Any]] = {}
    for step in layout.get("steps", []):
        if not isinstance(step, dict) or not isinstance(step.get("id"), int):
            continue
        for image in step.get("images", []):
            if isinstance(image, dict):
                layout_images[(step["id"], str(image.get("target_id", "")))] = image
    image_records: list[tuple[int, Path]] = []
    image_errors: list[str] = []
    for selection in normalized:
        for choice in selection.choices:
            path = (root / choice.frame_path).resolve()
            if root not in path.parents or not path.is_file() or path.stat().st_size == 0:
                image_errors.append(f"step {selection.step_id} selected image is missing: {choice.frame_path}")
                continue
            layout_image = layout_images.get((selection.step_id, choice.target_id))
            if layout_image is None or layout_image.get("path") != choice.frame_path:
                image_errors.append(
                    f"step {selection.step_id} selected image is absent from the rendered layout: {choice.target_id}"
                )
                continue
            digest = file_sha256(path)
            if layout_image.get("sha256") != digest:
                image_errors.append(f"step {selection.step_id} selected image hash does not match the layout")
                continue
            image_records.append((selection.step_id, path))
    duplicate_pairs: list[list[int]] = []
    measured = []
    for step_id, path in image_records:
        try:
            metrics = evaluate_image(path, min_score=0.0)
        except Exception as exc:
            image_errors.append(f"cannot inspect selected image {path.name}: {exc}")
            continue
        for previous_step, previous_metrics in measured:
            if are_near_duplicates(metrics, previous_metrics):
                pair = sorted((previous_step, step_id))
                if pair not in duplicate_pairs:
                    duplicate_pairs.append(pair)
        measured.append((step_id, metrics))

    blank_pages: list[int] = []
    edge_warnings: list[str] = []
    page_count = 0
    raster_error = ""
    if valid_signature:
        try:
            previews = rasterize_pdf(pdf, preview_dir or pdf.parent / "preview")
            page_count = len(previews)
            blank_pages, edge_warnings = _page_diagnostics(previews)
        except Exception as exc:
            raster_error = str(exc)
    warnings = list(edge_warnings)
    if not valid_signature:
        warnings.append("invalid PDF signature or EOF marker")
    if layout_error:
        warnings.append(f"invalid layout JSON: {layout_error}")
    if raster_error:
        warnings.append(f"PDF rasterization failed: {raster_error}")
    warnings.extend(image_errors)
    if duplicate_pairs:
        warnings.append("near-duplicate images were selected across steps")
    passed = (
        valid_signature
        and page_count > 0
        and not blank_pages
        and not unresolved
        and input_hash_matches
        and not missing_required
        and coverage >= 0.9
        and not image_errors
        and not duplicate_pairs
        and not raster_error
    )
    report = AuditReport(
        passed=passed,
        input_hash_matches=input_hash_matches,
        unresolved_placeholders=unresolved,
        missing_placeholder_ids=placeholder_ids,
        blank_pages=blank_pages,
        edge_warnings=warnings,
        duplicate_pairs=duplicate_pairs,
        missing_required_steps=missing_required,
        step_count=len(layout.get("steps", [])),
        image_count=len(image_records),
        page_count=page_count,
        coverage=round(coverage, 6),
    )
    report.validate()
    return report


def passes_review_gate(audit: AuditReport, review: ReviewReport) -> bool:
    """Apply the fixed combined deterministic/model acceptance threshold."""
    audit.validate()
    review.validate()
    return (
        audit.passed
        and audit.coverage >= 0.9
        and review.available
        and review.verdict == "pass"
        and review.overall >= 8.0
        and all(review.scores.get(name, 0.0) >= 7.0 for name in REVIEW_DIMENSIONS)
        and not any(issue.severity == "high" for issue in review.issues)
    )


def candidate_sort_key(report: IterationReport) -> tuple[Any, ...]:
    """Return a deterministic key where a larger value means a better candidate."""
    report.validate()
    scores = report.review.scores
    return (
        int(passes_review_gate(report.audit, report.review)),
        int(report.audit.passed),
        round(report.audit.coverage, 6),
        -sum(issue.severity == "high" for issue in report.review.issues),
        min((scores.get(name, 0.0) for name in REVIEW_DIMENSIONS), default=0.0),
        report.review.overall,
        -len(report.audit.blank_pages),
        -len(report.audit.duplicate_pairs),
        -report.iteration,
    )


def best_candidate(reports: list[IterationReport]) -> IterationReport | None:
    return max(reports, key=candidate_sort_key) if reports else None


def normalize_repair_actions(
    raw: Any,
    *,
    valid_step_ids: set[int] | None = None,
    valid_target_ids: set[str] | None = None,
    limit: int = 12,
) -> list[RepairAction]:
    """Strictly normalize model repair output and remove invalid or duplicate actions."""
    if isinstance(raw, dict):
        raw = raw.get("repairs", raw.get("actions", []))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("repair actions must be an array or an object containing actions")
    result: list[RepairAction] = []
    seen: set[tuple[Any, ...]] = set()
    for value in raw:
        try:
            action = value if isinstance(value, RepairAction) else RepairAction.parse(value)
        except (TypeError, ValueError):
            continue
        if valid_step_ids is not None and action.step_id is not None and action.step_id not in valid_step_ids:
            continue
        if valid_target_ids is not None and action.target_id and action.target_id not in valid_target_ids:
            continue
        if action.type in {"rewrite_text", "reselect_image"} and action.step_id is None:
            continue
        key = (action.type, action.step_id, action.target_id, action.instruction.strip())
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
        if len(result) >= limit:
            break
    return result


quality_gate = passes_review_gate
select_best_candidate = best_candidate
normalize_repairs = normalize_repair_actions
