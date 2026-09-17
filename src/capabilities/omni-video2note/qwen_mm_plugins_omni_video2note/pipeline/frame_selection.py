"""Coarse-to-fine frame candidates with deterministic gates and one batch review callback."""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .config import QualityProfile
from .image_quality import QualityMetrics, are_near_duplicates, evaluate_image
from .media import (
    ExtractedFrame,
    extract_coarse_frames,
    extract_frames,
    fine_sample_times,
    probe_video,
    sample_window_times,
    step_window,
)
from .schemas import (
    CandidateReview,
    CandidateTrial,
    FrameCandidate,
    FrameSelection,
    PlanStep,
    SelectionResult,
    StepSelection,
    VisualTarget,
    safe_relative_path,
)

ReviewCallback = Callable[[list[dict[str, Any]]], Any]


@dataclass(frozen=True)
class SelectionConfig:
    coarse_frames: int = 12
    candidate_limit: int = 48
    scene_threshold: float = 0.25
    step_padding: float = 2.0
    dense_step: float = 1.0
    fine_step: float = 0.2
    fine_radius: float = 0.8
    max_step_candidates: int = 10
    min_frame_score: float = 0.35
    min_relevance: float = 0.62

    @classmethod
    def from_profile(cls, profile: QualityProfile) -> SelectionConfig:
        return cls(
            coarse_frames=profile.coarse_frames,
            candidate_limit=profile.candidate_limit,
            scene_threshold=profile.scene_threshold,
            step_padding=profile.step_padding,
            dense_step=profile.dense_step,
            fine_step=profile.fine_step,
            fine_radius=profile.fine_radius,
            max_step_candidates=profile.max_step_candidates,
            min_frame_score=profile.min_frame_score,
            min_relevance=profile.min_relevance,
        )

    def validate(self) -> None:
        counts = (self.coarse_frames, self.candidate_limit, self.max_step_candidates)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
            raise TypeError("frame counts and limits must be integers")
        if self.coarse_frames < 2 or min(self.candidate_limit, self.max_step_candidates) < 1:
            raise ValueError("coarse_frames must be at least 2 and other limits must be positive")
        if self.candidate_limit < self.coarse_frames:
            raise ValueError("candidate_limit cannot be smaller than coarse_frames")
        intervals = (self.step_padding, self.dense_step, self.fine_step, self.fine_radius)
        if not all(math.isfinite(value) for value in intervals):
            raise ValueError("frame sampling intervals must be finite")
        if min(self.dense_step, self.fine_step, self.fine_radius) <= 0 or self.step_padding < 0:
            raise ValueError("frame sampling intervals must be positive and padding non-negative")
        for value in (self.scene_threshold, self.min_frame_score, self.min_relevance):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("selection thresholds must be finite and in [0, 1]")


@dataclass
class _Candidate:
    schema: FrameCandidate
    absolute_path: Path
    metrics: QualityMetrics


def candidate_times_r1(
    start: float,
    end: float,
    duration: float,
    padding: float,
    dense_step: float,
    limit: int,
) -> list[float]:
    window_start, window_end = step_window(start, end, duration, padding)
    return sample_window_times(window_start, window_end, duration, dense_step, limit)


def candidate_times_r2(center: float, duration: float, radius: float, fine_step: float, limit: int) -> list[float]:
    return fine_sample_times(center, duration, radius, fine_step, limit)


def filter_quality_candidates(
    items: list[tuple[float, Path]],
    min_score: float,
    existing: list[QualityMetrics] | None = None,
) -> list[dict[str, Any]]:
    accepted = []
    seen = list(existing or [])
    for timestamp, path in items:
        metrics = evaluate_image(path, min_score=min_score)
        if metrics.reject_reasons or any(are_near_duplicates(metrics, previous) for previous in seen):
            continue
        accepted.append({"timestamp": timestamp, "path": path, "metrics": metrics})
        seen.append(metrics)
    return accepted


class FrameSelector:
    """Generate R0/R1/R2 candidates and delegate semantic ranking exactly once per batch."""

    def __init__(
        self,
        video_path: str | Path,
        workdir: str | Path,
        *,
        duration: float | None = None,
        config: SelectionConfig | None = None,
    ) -> None:
        self.video_path = Path(video_path).expanduser().resolve()
        self.workdir = Path(workdir).expanduser().resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.duration = duration if duration is not None else probe_video(self.video_path).duration
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("video duration must be finite and positive")
        self.config = config or SelectionConfig()
        self.config.validate()
        self.tried: list[CandidateTrial] = []
        self.coarse_pool: list[_Candidate] = []
        self._quality_cache: dict[Path, QualityMetrics] = {}
        self._frame_cache: dict[float, ExtractedFrame] = {}

    def _relative(self, path: Path) -> str:
        try:
            return safe_relative_path(path.resolve().relative_to(self.workdir).as_posix())
        except ValueError as exc:
            raise ValueError(f"frame artifact is outside workdir: {path}") from exc

    def _quality(self, path: Path) -> QualityMetrics:
        key = path.resolve()
        if key not in self._quality_cache:
            self._quality_cache[key] = evaluate_image(key, min_score=self.config.min_frame_score)
        return self._quality_cache[key]

    def _record(
        self,
        *,
        step_id: int,
        candidate_id: str,
        timestamp: float,
        path: Path,
        round_name: str,
        outcome: str,
        metrics: QualityMetrics,
        target_id: str = "",
    ) -> None:
        self.tried.append(
            CandidateTrial(
                step_id=step_id,
                candidate_id=candidate_id,
                timestamp=round(timestamp, 3),
                path=self._relative(path),
                round_name=round_name,
                outcome=outcome,
                quality_score=metrics.score,
                target_id=target_id,
                reject_reasons=list(metrics.reject_reasons),
            )
        )

    def _candidate(
        self,
        step_id: int,
        target_ids: list[str],
        round_name: Literal["R0", "R1", "R2"],
        timestamp: float,
        path: Path,
        seen: list[QualityMetrics],
    ) -> _Candidate | None:
        candidate_id = f"s{step_id:03d}-{round_name.lower()}-{round(timestamp * 1000):012d}"
        metrics = self._quality(path)
        if metrics.reject_reasons:
            self._record(
                step_id=step_id,
                candidate_id=candidate_id,
                timestamp=timestamp,
                path=path,
                round_name=round_name,
                outcome="quality_rejected",
                metrics=metrics,
            )
            return None
        if any(are_near_duplicates(metrics, previous) for previous in seen):
            self._record(
                step_id=step_id,
                candidate_id=candidate_id,
                timestamp=timestamp,
                path=path,
                round_name=round_name,
                outcome="same_step_duplicate",
                metrics=metrics,
            )
            return None
        schema = FrameCandidate(
            id=candidate_id,
            step_id=step_id,
            target_ids=target_ids,
            timestamp=round(timestamp, 3),
            path=self._relative(path),
            round_name=round_name,
            quality_score=metrics.score,
        )
        schema.validate()
        seen.append(metrics)
        self._record(
            step_id=step_id,
            candidate_id=candidate_id,
            timestamp=timestamp,
            path=path,
            round_name=round_name,
            outcome="candidate",
            metrics=metrics,
        )
        return _Candidate(schema, path, metrics)

    def prepare_coarse(self) -> list[FrameCandidate]:
        frames = extract_coarse_frames(
            self.video_path,
            self.workdir / "frames" / "r0",
            max_frames=self.config.coarse_frames,
            candidate_limit=self.config.candidate_limit,
            scene_threshold=self.config.scene_threshold,
        )
        self.coarse_pool = []
        seen: list[QualityMetrics] = []
        for frame in frames:
            self._frame_cache[frame.timestamp] = frame
            candidate = self._candidate(0, ["coarse"], "R0", frame.timestamp, frame.path, seen)
            if candidate is not None:
                self.coarse_pool.append(candidate)
        return [candidate.schema for candidate in self.coarse_pool]

    def restore_coarse(self, candidates: list[FrameCandidate]) -> None:
        self.coarse_pool = []
        for candidate in candidates:
            candidate.validate()
            path = (self.workdir / candidate.path).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"coarse frame is missing: {candidate.path}")
            self.coarse_pool.append(_Candidate(candidate, path, self._quality(path)))
            self._frame_cache[candidate.timestamp] = ExtractedFrame(candidate.timestamp, path, "restored")

    def _normalize_step(self, value: PlanStep | dict[str, Any]) -> PlanStep:
        if isinstance(value, PlanStep):
            value.validate()
            return value
        return PlanStep.parse(value)

    def _extract_times(self, timestamps: list[float]) -> list[ExtractedFrame]:
        normalized = list(dict.fromkeys(round(timestamp, 3) for timestamp in timestamps))
        missing = [timestamp for timestamp in normalized if timestamp not in self._frame_cache]
        if missing:
            frames = extract_frames(
                self.video_path,
                missing,
                self.workdir / "frames" / "candidates",
                prefix="frame",
                width=1280,
            )
            self._frame_cache.update({frame.timestamp: frame for frame in frames})
            for requested in missing:
                self._frame_cache[requested] = min(
                    frames,
                    key=lambda frame: abs(frame.timestamp - requested),
                )
        return [self._frame_cache[timestamp] for timestamp in normalized]

    def _step_candidates(self, step: PlanStep) -> list[_Candidate]:
        target_ids = [target.id for target in step.visual_targets]
        window_start, window_end = step_window(step.start, step.end, self.duration, self.config.step_padding)
        local_coarse = [
            candidate
            for candidate in self.coarse_pool
            if window_start <= candidate.schema.timestamp <= window_end
        ]
        global_coarse = [
            candidate
            for candidate in self.coarse_pool
            if not window_start <= candidate.schema.timestamp <= window_end
        ]
        coarse_limit = max(1, self.config.max_step_candidates // 3)
        ordered_coarse = sorted(local_coarse, key=lambda item: item.metrics.score, reverse=True)
        ordered_coarse += sorted(global_coarse, key=lambda item: item.metrics.score, reverse=True)
        selected_coarse = ordered_coarse[:coarse_limit]
        accepted: list[_Candidate] = []
        seen: list[QualityMetrics] = []
        for source in selected_coarse:
            candidate = self._candidate(
                step.id,
                target_ids,
                "R0",
                source.schema.timestamp,
                source.absolute_path,
                seen,
            )
            if candidate is not None:
                accepted.append(candidate)

        r1_times = candidate_times_r1(
            step.start,
            step.end,
            self.duration,
            self.config.step_padding,
            self.config.dense_step,
            self.config.max_step_candidates,
        )
        r1_frames = self._extract_times(r1_times)
        for frame in r1_frames:
            candidate = self._candidate(step.id, target_ids, "R1", frame.timestamp, frame.path, seen)
            if candidate is not None:
                accepted.append(candidate)

        center = (
            max(accepted, key=lambda item: item.metrics.score).schema.timestamp
            if accepted
            else (step.start + step.end) / 2
        )
        # A borrowed global coarse frame can score best, so refine inside the step's own window only.
        center = min(max(center, window_start), window_end)
        r2_times = candidate_times_r2(
            center,
            self.duration,
            self.config.fine_radius,
            self.config.fine_step,
            self.config.max_step_candidates,
        )
        used_times = {candidate.schema.timestamp for candidate in selected_coarse}
        used_times.update(frame.timestamp for frame in r1_frames)
        r2_times = [
            timestamp
            for timestamp in r2_times
            if round(timestamp, 3) not in used_times and window_start <= timestamp <= window_end
        ]
        r2_frames = self._extract_times(r2_times)
        for frame in r2_frames:
            candidate = self._candidate(step.id, target_ids, "R2", frame.timestamp, frame.path, seen)
            if candidate is not None:
                accepted.append(candidate)
        return sorted(
            sorted(accepted, key=lambda item: item.metrics.score, reverse=True)[: self.config.max_step_candidates],
            key=lambda item: item.schema.timestamp,
        )

    @staticmethod
    def _flatten_reviews(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, dict):
            raw = raw.get("reviews", raw.get("steps", raw.get("targets")))
        if not isinstance(raw, list):
            raise TypeError("batch review callback must return a list or an object containing reviews")
        flattened: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, CandidateReview):
                flattened.append(item.to_dict())
                continue
            if not isinstance(item, dict):
                raise TypeError("batch review entries must be objects")
            if "targets" in item:
                if not isinstance(item["targets"], list):
                    raise TypeError("review targets must be an array")
                for target in item["targets"]:
                    if not isinstance(target, dict):
                        raise TypeError("review target must be an object")
                    flattened.append({"step_id": item.get("step_id"), **target})
            else:
                flattened.append(item)
        return flattened

    def select_steps(
        self,
        steps: list[PlanStep | dict[str, Any]],
        review_callback: ReviewCallback,
    ) -> SelectionResult:
        """Select frames for all steps; ``review_callback`` is invoked exactly once."""
        normalized_steps = [self._normalize_step(step) for step in steps]
        if not normalized_steps:
            raise ValueError("at least one plan step is required")
        if len({step.id for step in normalized_steps}) != len(normalized_steps):
            raise ValueError("plan step ids must be unique")
        if not callable(review_callback):
            raise TypeError("review_callback must be callable")
        if not self.coarse_pool:
            self.prepare_coarse()
        candidates_by_step = {step.id: self._step_candidates(step) for step in normalized_steps}
        requests = []
        for step in normalized_steps:
            requests.append(
                {
                    "step": step.to_dict(),
                    "candidates": [
                        candidate.schema.to_dict()
                        | {
                            "absolute_path": str(candidate.absolute_path),
                            "quality": candidate.metrics.to_dict(),
                        }
                        for candidate in candidates_by_step[step.id]
                    ],
                    "minimum_relevance": self.config.min_relevance,
                }
            )
        raw_reviews = review_callback(requests)
        reviews = [CandidateReview.parse(item) for item in self._flatten_reviews(raw_reviews)]
        expected = {(step.id, target.id) for step in normalized_steps for target in step.visual_targets}
        received = {(review.step_id, review.target_id) for review in reviews}
        if received != expected or len(reviews) != len(expected):
            raise ValueError("batch review must return exactly one result for every visual target")
        review_map = {(review.step_id, review.target_id): review for review in reviews}
        selected_metrics: list[QualityMetrics] = []
        selections: list[StepSelection] = []
        selected_dir = self.workdir / "frames" / "selected"
        selected_dir.mkdir(parents=True, exist_ok=True)
        for step in normalized_steps:
            lookup = {candidate.schema.id: candidate for candidate in candidates_by_step[step.id]}
            choices: list[FrameSelection] = []
            misses: dict[str, str] = {}
            step_metrics: list[QualityMetrics] = []
            for target in step.visual_targets:
                review = review_map[(step.id, target.id)]
                if review.selected_id is None or review.relevance < self.config.min_relevance:
                    misses[target.id] = review.reason or "no candidate passed the relevance gate"
                    continue
                ranking = list(dict.fromkeys([review.selected_id, *review.ranked_ids]))
                chosen: _Candidate | None = None
                for candidate_id in ranking:
                    candidate = lookup.get(candidate_id)
                    if candidate is None:
                        raise ValueError(f"review selected unknown candidate: {candidate_id}")
                    if any(are_near_duplicates(candidate.metrics, metric) for metric in step_metrics):
                        self._record(
                            step_id=step.id,
                            candidate_id=candidate_id,
                            timestamp=candidate.schema.timestamp,
                            path=candidate.absolute_path,
                            round_name=candidate.schema.round_name,
                            outcome="same_step_selection_duplicate",
                            metrics=candidate.metrics,
                            target_id=target.id,
                        )
                        continue
                    if any(are_near_duplicates(candidate.metrics, metric) for metric in selected_metrics):
                        self._record(
                            step_id=step.id,
                            candidate_id=candidate_id,
                            timestamp=candidate.schema.timestamp,
                            path=candidate.absolute_path,
                            round_name=candidate.schema.round_name,
                            outcome="cross_step_duplicate",
                            metrics=candidate.metrics,
                            target_id=target.id,
                        )
                        continue
                    chosen = candidate
                    break
                if chosen is None:
                    misses[target.id] = "reviewed candidates duplicate evidence already selected"
                    continue
                destination = selected_dir / f"step-{step.id:03d}-{target.id}.jpg"
                shutil.copy2(chosen.absolute_path, destination)
                choice = FrameSelection(
                    step_id=step.id,
                    target_id=target.id,
                    role=target.role,
                    frame_path=self._relative(destination),
                    timestamp=chosen.schema.timestamp,
                    relevance=review.relevance,
                    reason=review.reason or "quality and relevance gates passed",
                    round_name=chosen.schema.round_name,
                    caption=review.caption,
                    candidate_id=chosen.schema.id,
                )
                choice.validate()
                choices.append(choice)
                step_metrics.append(chosen.metrics)
                selected_metrics.append(chosen.metrics)
                self._record(
                    step_id=step.id,
                    candidate_id=chosen.schema.id,
                    timestamp=chosen.schema.timestamp,
                    path=chosen.absolute_path,
                    round_name=chosen.schema.round_name,
                    outcome="selected",
                    metrics=chosen.metrics,
                    target_id=target.id,
                )
            selection = StepSelection(step.id, choices, misses)
            selection.validate()
            selections.append(selection)
        result = SelectionResult(selections=selections, tried=list(self.tried))
        result.validate()
        return result

    def select_for_step(
        self,
        step: PlanStep | dict[str, Any],
        review_callback: ReviewCallback,
    ) -> StepSelection:
        return self.select_steps([step], review_callback).selections[0]

    def tried_dicts(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.tried]


def review_request_example(step: PlanStep, candidates: list[FrameCandidate]) -> dict[str, Any]:
    """Build the callback payload shape without invoking a model."""
    return {
        "step": step.to_dict(),
        "targets": [asdict(target) if isinstance(target, VisualTarget) else target for target in step.visual_targets],
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
