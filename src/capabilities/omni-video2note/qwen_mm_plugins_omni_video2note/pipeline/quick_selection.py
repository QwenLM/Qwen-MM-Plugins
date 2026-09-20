"""Bounded local screenshot selection within each step's source time range."""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .config import PipelineConfig
from .image_quality import evaluate_image
from .media import extract_frame
from .schemas import (
    CandidateTrial,
    DocumentPlan,
    FrameSelection,
    PlanStep,
    ProbeResult,
    SelectionResult,
    StepSelection,
)


@dataclass(frozen=True)
class _Sample:
    timestamp: float
    path: Path
    score: float
    reject_reasons: tuple[str, ...]
    quality_available: bool


def _step_times(step: PlanStep, probe: ProbeResult) -> list[float]:
    """Sample only inside a usable part of the step; never borrow unrelated frames."""
    if not all(math.isfinite(value) for value in (step.start, step.end)):
        return []
    last_frame = max(0.0, probe.duration - max(0.1, 1.0 / probe.fps))
    start, end = max(0.0, step.start), min(step.end, last_frame)
    if end < start:
        return []
    span = end - start
    # Center first so it remains available if the local extraction budget runs out.
    return list(dict.fromkeys(round(start + span * fraction, 6) for fraction in (0.5, 0.25, 0.75)))


def _caption(timestamp: float, title: str) -> str:
    seconds = int(timestamp)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    clock = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    return f"{clock} · {title}"


def select_timeline_frames(
    config: PipelineConfig,
    plan: DocumentPlan,
    probe: ProbeResult,
    workdir: str | Path,
) -> SelectionResult:
    """Choose at most one local screenshot per step, without any model requests.

    At most three positions per step are sampled by four workers. Failed screenshots
    do not prevent PDF creation. The shared extraction budget is 12 seconds; each
    ffmpeg call is also limited to four seconds. Image quality only ranks readable
    samples: it does not impose a hard quality or semantic relevance gate.
    """
    root = Path(workdir).expanduser().resolve()
    directory = root / "frames" / "timeline"
    deadline = time.monotonic() + 12.0
    times_by_step = {step.id: _step_times(step, probe) for step in plan.steps}
    # Interleave ranks across steps: every step gets its center before alternatives.
    timestamps = list(
        dict.fromkeys(times[index] for index in range(3) for times in times_by_step.values() if index < len(times))
    )

    def sample(timestamp: float) -> _Sample | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        path = directory / f"frame-{round(timestamp * 1_000_000):015d}.jpg"
        try:
            extract_frame(
                config.video,
                timestamp,
                path,
                width=min(1280, probe.width),
                timeout=min(4.0, remaining),
                _probe_result=probe,
            )
        except Exception:
            return None
        try:
            metrics = evaluate_image(path, min_score=0.0)
            return _Sample(timestamp, path, metrics.score, metrics.reject_reasons, True)
        except Exception:
            # A metric error must not discard an otherwise readable screenshot.
            try:
                from PIL import Image

                with Image.open(path) as image:
                    image.verify()
            except Exception:
                return None
            return _Sample(timestamp, path, 0.0, (), False)

    with ThreadPoolExecutor(max_workers=min(4, len(timestamps) or 1)) as executor:
        samples = dict(zip(timestamps, executor.map(sample, timestamps)))

    selections: list[StepSelection] = []
    tried: list[CandidateTrial] = []
    for step in plan.steps:
        primary = next(target for target in step.visual_targets if target.role == "primary")
        misses = {
            target.id: "Additional screenshots are omitted in timeline selection."
            for target in step.visual_targets
            if target.id != primary.id
        }
        available = [samples[timestamp] for timestamp in times_by_step[step.id] if samples.get(timestamp)]
        if not available:
            reason = "No readable screenshot was available within this step's time range."
            misses[primary.id] = reason
            selections.append(StepSelection(step_id=step.id, misses=misses))
            config.warnings.append(f"Step {step.id}: {reason} Text was preserved.")
            continue
        chosen = max(available, key=lambda item: (not item.reject_reasons, item.score))
        for item in available:
            tried.append(
                CandidateTrial(
                    step_id=step.id,
                    candidate_id=f"s{step.id:03d}-timeline-{round(item.timestamp * 1_000_000):015d}",
                    timestamp=item.timestamp,
                    path=item.path.relative_to(root).as_posix(),
                    round_name="R1",
                    outcome="selected" if item is chosen else "candidate",
                    quality_score=item.score,
                    target_id=primary.id,
                    reject_reasons=list(item.reject_reasons),
                )
            )
        if not chosen.quality_available:
            config.warnings.append(f"Step {step.id}: screenshot is readable; local image quality metrics unavailable.")
        elif chosen.reject_reasons:
            config.warnings.append(f"Step {step.id}: screenshot retained despite limited image clarity.")
        choice = FrameSelection(
            step_id=step.id,
            target_id=primary.id,
            role="primary",
            frame_path=chosen.path.relative_to(root).as_posix(),
            timestamp=chosen.timestamp,
            # No model has evaluated semantic relevance. Zero must not masquerade as a score.
            relevance=0.0,
            reason="Selected inside the step time range by local image quality; semantic relevance was not evaluated.",
            round_name="R1",
            caption=_caption(chosen.timestamp, step.title),
            candidate_id=f"s{step.id:03d}-timeline-{round(chosen.timestamp * 1_000_000):015d}",
        )
        selections.append(StepSelection(step_id=step.id, choices=[choice], misses=misses))
    result = SelectionResult(selections=selections, tried=tried)
    result.validate()
    return result
