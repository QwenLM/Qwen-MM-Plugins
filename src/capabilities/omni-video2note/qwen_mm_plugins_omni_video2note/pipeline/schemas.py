"""Strict, JSON-serializable data contracts for the Video2Note pipeline."""

from __future__ import annotations

import math
import types
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from pathlib import PurePosixPath
from typing import Any, ClassVar, Literal, get_args, get_origin, get_type_hints

# A plan drives frame extraction, review calls, and pages, so its step count is hard-bounded.
MAX_PLAN_STEPS = 30


def safe_relative_path(value: str) -> str:
    """Return a normalized workdir-relative path, rejecting traversal and absolute paths."""
    if not isinstance(value, str):
        raise TypeError("artifact path must be a string")
    text = value.replace("\\", "/")
    path = PurePosixPath(text)
    if text in {"", "."} or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe workdir-relative path: {value!r}")
    return path.as_posix()


def _json_value(value: Any) -> Any:
    if isinstance(value, StrictSchema):
        return value.to_dict()
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _parse_value(annotation: Any, value: Any, location: str) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is Any:
        return value
    if origin in (types.UnionType, getattr(__import__("typing"), "Union")):
        errors = []
        for option in args:
            try:
                return _parse_value(option, value, location)
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
        raise TypeError(f"{location} does not match any allowed type: {'; '.join(errors)}")
    if origin is Literal:
        if value not in args or any(type(value) is not type(item) for item in args if value == item):
            raise ValueError(f"{location} must be one of {args!r}")
        return value
    if origin is list:
        if not isinstance(value, list):
            raise TypeError(f"{location} must be an array")
        return [_parse_value(args[0], item, f"{location}[{index}]") for index, item in enumerate(value)]
    if origin is dict:
        if not isinstance(value, dict):
            raise TypeError(f"{location} must be an object")
        return {
            _parse_value(args[0], key, f"{location}.<key>"): _parse_value(args[1], item, f"{location}.{key}")
            for key, item in value.items()
        }
    if isinstance(annotation, type) and issubclass(annotation, StrictSchema):
        if isinstance(value, annotation):
            value.validate()
            return value
        return annotation.parse(value)
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{location} must be a number")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{location} must be finite")
        return result
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{location} must be an integer")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise TypeError(f"{location} must be a boolean")
        return value
    if annotation is str:
        if not isinstance(value, str):
            raise TypeError(f"{location} must be a string")
        return value
    if not isinstance(value, annotation):
        raise TypeError(f"{location} must be {annotation}")
    return value


class StrictSchema:
    """Dataclass mixin with strict parsing, recursive validation, and JSON conversion."""

    _allow_unknown: ClassVar[bool] = False

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        validator = cls.__dict__.get("validate")
        if validator is None:
            return

        def strict_validator(self: StrictSchema) -> None:
            StrictSchema.validate(self)
            validator(self)

        setattr(cls, "validate", strict_validator)

    def validate(self) -> None:
        hints = get_type_hints(type(self))
        for item in fields(self):
            value = _parse_value(hints[item.name], getattr(self, item.name), f"{type(self).__name__}.{item.name}")
            setattr(self, item.name, value)

    @classmethod
    def parse(cls, data: dict[str, Any]) -> Any:
        if not isinstance(data, dict):
            raise TypeError(f"{cls.__name__} must be parsed from an object")
        declared = {item.name: item for item in fields(cls)}
        unknown = set(data) - set(declared)
        if unknown and not cls._allow_unknown:
            raise ValueError(f"unknown {cls.__name__} field(s): {', '.join(sorted(unknown))}")
        hints = get_type_hints(cls)
        values: dict[str, Any] = {}
        for name, item in declared.items():
            if name not in data:
                if item.default is MISSING and item.default_factory is MISSING:
                    raise ValueError(f"missing {cls.__name__}.{name}")
                continue
            values[name] = _parse_value(hints[name], data[name], f"{cls.__name__}.{name}")
        result = cls(**values)
        result.validate()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Any:
        return cls.parse(data)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {item.name: _json_value(getattr(self, item.name)) for item in fields(self)}


def _required_text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} is required")


def _time_range(start: float, end: float, name: str) -> None:
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
        raise ValueError(f"{name} times must be finite, non-negative, and ordered")


def _score(value: float, name: str, upper: float = 1.0) -> None:
    if not math.isfinite(value) or not 0 <= value <= upper:
        raise ValueError(f"{name} must be in [0, {upper:g}]")


@dataclass
class ProbeResult(StrictSchema):
    path: str
    size_bytes: int
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    audio_codec: str = ""
    format_name: str = ""
    rotation: int = 0
    video_stream_index: int = 0

    def validate(self) -> None:
        _required_text("probe path", self.path)
        if self.size_bytes <= 0 or self.duration <= 0 or self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ValueError("probe requires positive size, duration, dimensions, and fps")
        if self.video_stream_index < 0:
            raise ValueError("video stream index must be non-negative")
        _required_text("video codec", self.video_codec)


@dataclass
class TranscriptSegment(StrictSchema):
    start: float
    end: float
    text: str
    speaker: str = ""

    def validate(self) -> None:
        _time_range(self.start, self.end, "transcript segment")
        _required_text("transcript text", self.text)


@dataclass
class Transcript(StrictSchema):
    language: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    text: str = ""
    error: str = ""

    def validate(self) -> None:
        _required_text("transcript language", self.language)
        previous = -1.0
        for segment in self.segments:
            segment.validate()
            if segment.start < previous:
                raise ValueError("transcript segments must be time ordered")
            previous = segment.start


@dataclass
class TimedEvent(StrictSchema):
    start: float
    end: float
    fact: str

    def validate(self) -> None:
        _time_range(self.start, self.end, "event")
        _required_text("event fact", self.fact)


@dataclass
class VideoUnderstanding(StrictSchema):
    language: str
    subject: str
    summary: str
    events: list[TimedEvent]
    audience: str = ""
    prerequisites: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    safety: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    visible_terms: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for name in ("language", "subject", "summary"):
            _required_text(name, getattr(self, name))
        previous = -1.0
        for event in self.events:
            event.validate()
            if event.start < previous:
                raise ValueError("events must be time ordered")
            previous = event.start


@dataclass
class VisualTarget(StrictSchema):
    id: str
    role: Literal["primary", "supporting"]
    query: str

    def validate(self) -> None:
        _required_text("visual target id", self.id)
        _required_text("visual target query", self.query)
        if not all(character.isalnum() or character in "-_" for character in self.id):
            raise ValueError("visual target id contains unsafe characters")


@dataclass
class PlanStep(StrictSchema):
    id: int
    title: str
    objective: str
    start: float
    end: float
    visual_targets: list[VisualTarget]
    image_required: bool = True

    def validate(self) -> None:
        if self.id < 1:
            raise ValueError("step id must be positive")
        _required_text("step title", self.title)
        _required_text("step objective", self.objective)
        _time_range(self.start, self.end, "step")
        if not 1 <= len(self.visual_targets) <= 2:
            raise ValueError("a step needs one primary and at most one supporting visual target")
        roles = [target.role for target in self.visual_targets]
        if roles.count("primary") != 1 or roles.count("supporting") > 1:
            raise ValueError("visual targets need exactly one primary and at most one supporting role")
        if len({target.id for target in self.visual_targets}) != len(self.visual_targets):
            raise ValueError("visual target ids must be unique within a step")
        super().validate()

    @property
    def visual_query(self) -> str:
        return next(target.query for target in self.visual_targets if target.role == "primary")


@dataclass
class DocumentPlan(StrictSchema):
    title: str
    audience: str
    overview_goal: str
    steps: list[PlanStep]
    prerequisites: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    safety: list[str] = field(default_factory=list)
    common_mistakes: list[str] = field(default_factory=list)
    completion_checks: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for name in ("title", "audience", "overview_goal"):
            _required_text(name, getattr(self, name))
        if not self.steps:
            raise ValueError("document plan requires at least one step")
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(f"document plan must not exceed {MAX_PLAN_STEPS} steps")
        if [step.id for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("step ids must be continuous from 1")
        previous = -1.0
        for step in self.steps:
            step.validate()
            if step.start < previous:
                raise ValueError("plan steps must be time ordered")
            previous = step.start


@dataclass
class DraftStep(StrictSchema):
    id: int
    title: str
    instruction: str
    details: list[str] = field(default_factory=list)
    caption: str = ""

    def validate(self) -> None:
        if self.id < 1:
            raise ValueError("draft step id must be positive")
        _required_text("draft step title", self.title)
        _required_text("draft step instruction", self.instruction)
        content = "\n".join((self.title, self.instruction, self.caption, *self.details)).lower()
        if "{{frame" in content:
            raise ValueError("draft fields must not contain frame placeholders")


@dataclass
class DocumentDraft(StrictSchema):
    title: str
    overview: str
    steps: list[DraftStep]
    prerequisites: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    safety: list[str] = field(default_factory=list)
    common_mistakes: list[str] = field(default_factory=list)
    closing: str = ""

    def validate(self) -> None:
        _required_text("draft title", self.title)
        _required_text("draft overview", self.overview)
        if not self.steps:
            raise ValueError("draft requires at least one step")
        if [step.id for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("draft step ids must be continuous from 1")
        super().validate()

    def validate_against(self, plan: DocumentPlan) -> None:
        self.validate()
        if [step.id for step in self.steps] != [step.id for step in plan.steps]:
            raise ValueError("draft steps must exactly match plan step ids")


@dataclass
class FrameCandidate(StrictSchema):
    id: str
    step_id: int
    target_ids: list[str]
    timestamp: float
    path: str
    round_name: Literal["R0", "R1", "R2"]
    quality_score: float
    reject_reasons: list[str] = field(default_factory=list)

    def validate(self) -> None:
        _required_text("candidate id", self.id)
        if self.step_id < 0 or self.timestamp < 0:
            raise ValueError("candidate step and timestamp must be non-negative")
        if not self.target_ids:
            raise ValueError("candidate requires at least one target id")
        self.path = safe_relative_path(self.path)
        _score(self.quality_score, "candidate quality score")


@dataclass
class CandidateReview(StrictSchema):
    step_id: int
    target_id: str
    selected_id: str | None
    relevance: float
    reason: str
    caption: str = ""
    ranked_ids: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.step_id < 1:
            raise ValueError("review step id must be positive")
        _required_text("review target id", self.target_id)
        _score(self.relevance, "candidate relevance")
        if self.selected_id is not None:
            _required_text("selected candidate id", self.selected_id)


@dataclass
class FrameSelection(StrictSchema):
    step_id: int
    target_id: str
    role: Literal["primary", "supporting"]
    frame_path: str
    timestamp: float
    relevance: float
    reason: str
    round_name: Literal["R0", "R1", "R2"]
    caption: str = ""
    candidate_id: str = ""

    def validate(self) -> None:
        if self.step_id < 1 or self.timestamp < 0:
            raise ValueError("selection requires positive step id and non-negative timestamp")
        _required_text("selection target id", self.target_id)
        self.frame_path = safe_relative_path(self.frame_path)
        _score(self.relevance, "selection relevance")


@dataclass
class StepSelection(StrictSchema):
    step_id: int
    choices: list[FrameSelection] = field(default_factory=list)
    misses: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if self.step_id < 1 or len(self.choices) > 2:
            raise ValueError("step selection requires a positive id and at most two images")
        if len({choice.target_id for choice in self.choices}) != len(self.choices):
            raise ValueError("a target may have at most one selected image")
        if sum(choice.role == "primary" for choice in self.choices) > 1:
            raise ValueError("a step may have at most one primary image")
        for choice in self.choices:
            choice.validate()
            if choice.step_id != self.step_id:
                raise ValueError("selection step id does not match its container")
        if set(self.misses).intersection(choice.target_id for choice in self.choices):
            raise ValueError("a target cannot be both selected and missed")


@dataclass
class CandidateTrial(StrictSchema):
    step_id: int
    candidate_id: str
    timestamp: float
    path: str
    round_name: str
    outcome: str
    quality_score: float
    target_id: str = ""
    reject_reasons: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.step_id < 0 or self.timestamp < 0:
            raise ValueError("trial step and timestamp must be non-negative")
        self.path = safe_relative_path(self.path)
        _score(self.quality_score, "trial quality score")
        _required_text("trial outcome", self.outcome)


@dataclass
class SelectionResult(StrictSchema):
    selections: list[StepSelection]
    tried: list[CandidateTrial]


@dataclass
class AuditReport(StrictSchema):
    passed: bool
    input_hash_matches: bool
    unresolved_placeholders: bool
    missing_placeholder_ids: list[int] = field(default_factory=list)
    blank_pages: list[int] = field(default_factory=list)
    edge_warnings: list[str] = field(default_factory=list)
    duplicate_pairs: list[list[int]] = field(default_factory=list)
    missing_required_steps: list[int] = field(default_factory=list)
    step_count: int = 0
    image_count: int = 0
    page_count: int = 0
    coverage: float = 0.0

    def validate(self) -> None:
        if min(self.step_count, self.image_count, self.page_count) < 0:
            raise ValueError("audit counts must be non-negative")
        _score(self.coverage, "audit coverage")


@dataclass
class ReviewIssue(StrictSchema):
    dimension: str
    severity: Literal["low", "medium", "high"]
    location: str
    description: str
    suggestion: str = ""

    def validate(self) -> None:
        for name in ("dimension", "location", "description"):
            _required_text(name, getattr(self, name))


@dataclass
class ReviewReport(StrictSchema):
    verdict: Literal["pass", "repair", "best_effort"]
    overall: float
    scores: dict[str, float]
    issues: list[ReviewIssue] = field(default_factory=list)
    summary: str = ""
    available: bool = True

    def validate(self) -> None:
        _score(self.overall, "review overall", 10.0)
        for name, value in self.scores.items():
            _required_text("review score name", name)
            _score(value, f"review score {name}", 10.0)
        super().validate()


@dataclass
class RepairAction(StrictSchema):
    type: Literal["replan_document", "rewrite_text", "reselect_image", "adjust_layout"]
    instruction: str
    step_id: int | None = None
    target_id: str = ""

    def validate(self) -> None:
        _required_text("repair instruction", self.instruction)
        if self.step_id is not None and self.step_id < 1:
            raise ValueError("repair step id must be positive")


@dataclass
class IterationReport(StrictSchema):
    iteration: int
    candidate_pdf: str
    audit: AuditReport
    review: ReviewReport
    repairs: list[RepairAction] = field(default_factory=list)
    complete: bool = False
    stop_reason: str = ""

    def validate(self) -> None:
        if self.iteration < 1:
            raise ValueError("iteration must be positive")
        self.candidate_pdf = safe_relative_path(self.candidate_pdf)
        super().validate()


@dataclass
class FinalReport(StrictSchema):
    status: Literal["pass", "best_effort", "failed"]
    passed: bool
    final_pdf: str | None
    best_iteration: int | None
    best_score: float
    iterations: list[IterationReport] = field(default_factory=list)
    error: str = ""

    def validate(self) -> None:
        if self.final_pdf is not None:
            self.final_pdf = safe_relative_path(self.final_pdf)
        if self.best_iteration is not None and self.best_iteration < 1:
            raise ValueError("best iteration must be positive")
        _score(self.best_score, "best score", 10.0)
        if self.status == "pass" and not self.passed:
            raise ValueError("pass status requires passed=true")
        if self.status == "failed" and self.passed:
            raise ValueError("failed status cannot have passed=true")
        if self.status in {"pass", "best_effort"} and self.final_pdf is None:
            raise ValueError("completed final report requires a final PDF")
        if self.best_iteration is not None and self.iterations:
            if self.best_iteration not in {item.iteration for item in self.iterations}:
                raise ValueError("best iteration is absent from iteration history")
        super().validate()


FrameChoice = FrameSelection
StepFrameChoices = StepSelection
Understanding = VideoUnderstanding
Event = TimedEvent
