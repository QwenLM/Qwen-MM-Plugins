"""Validated runtime configuration and deterministic quality profiles."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.api_omni import resolve_omni_model
from shared.api_openai import resolve_openai_endpoint, resolve_vl_model

_LOCAL_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts", ".m2ts", ".mpg", ".mpeg"}
)
_URL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
# output_path belongs to the fingerprint: a resume must stay bound to the PDF it was started for.
FINGERPRINT_EXCLUDED_KEYS = frozenset({"resume", "overwrite", "dry_run", "workdir"})
# Repair loops are bounded so one job can never spend an unbounded number of model calls.
MAX_ITERATIONS_LIMIT = 8


@dataclass(frozen=True)
class QualityProfile:
    """Sampling and review limits selected by a named quality profile."""

    name: str
    coarse_frames: int
    candidate_limit: int
    scene_threshold: float
    step_padding: float
    dense_step: float
    fine_step: float
    fine_radius: float
    max_step_candidates: int
    min_frame_score: float
    min_relevance: float
    chunk_seconds: float
    default_iterations: int

    def validate(self) -> None:
        if not self.name:
            raise ValueError("quality profile name is required")
        for name in ("coarse_frames", "candidate_limit", "max_step_candidates", "default_iterations"):
            if getattr(self, name) < 1:
                raise ValueError(f"quality profile {name} must be positive")
        for name in ("step_padding", "dense_step", "fine_step", "fine_radius", "chunk_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"quality profile {name} must be finite and positive")
        for name in ("scene_threshold", "min_frame_score", "min_relevance"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"quality profile {name} must be in [0, 1]")
        if self.candidate_limit < self.coarse_frames:
            raise ValueError("candidate_limit cannot be smaller than coarse_frames")


QUALITY_PROFILES: dict[str, QualityProfile] = {
    "fast": QualityProfile("fast", 8, 28, 0.32, 1.5, 1.5, 0.35, 0.7, 7, 0.32, 0.58, 120.0, 2),
    "balanced": QualityProfile("balanced", 12, 48, 0.25, 2.0, 1.0, 0.2, 0.8, 10, 0.35, 0.62, 90.0, 3),
    "high": QualityProfile("high", 18, 72, 0.2, 3.0, 0.6, 0.12, 1.0, 16, 0.38, 0.66, 60.0, 4),
}


def resolve_quality_profile(name: str) -> QualityProfile:
    normalized = name.strip().lower()
    try:
        profile = QUALITY_PROFILES[normalized]
    except KeyError as exc:
        choices = ", ".join(sorted(QUALITY_PROFILES))
        raise ValueError(f"unknown quality profile {name!r}; choose one of: {choices}") from exc
    profile.validate()
    return profile


def _optional_path(value: str | Path | None) -> Path | None:
    return None if value in (None, "") else Path(value).expanduser().resolve()


@dataclass
class PipelineConfig:
    """Resolved pipeline settings with safe local input/output paths."""

    video_path: str | Path
    output_path: str | Path
    workdir: str | Path | None = None
    language: str = "zh-CN"
    resume: bool = False
    overwrite: bool = False
    quality_profile: str = "balanced"
    max_iterations: int | None = None
    omni_model: str | None = None
    vl_model: str | None = None
    review_model: str | None = None
    font: str | Path | None = None
    bold_font: str | Path | None = None
    no_asr: bool = False
    require_asr: bool = False
    dry_run: bool = False
    profile: QualityProfile = field(init=False, repr=False)

    def __post_init__(self) -> None:
        raw_video = str(self.video_path)
        if _URL_PATTERN.match(raw_video):
            raise ValueError("URLs are not accepted; provide one local video file")
        self.video_path = Path(raw_video).expanduser().resolve()
        self.output_path = Path(self.output_path).expanduser().resolve()
        self.workdir = (
            Path(self.workdir).expanduser().resolve()
            if self.workdir is not None
            else self.output_path.with_suffix(self.output_path.suffix + ".work")
        )
        self.font = _optional_path(self.font)
        self.bold_font = _optional_path(self.bold_font)
        self.profile = resolve_quality_profile(self.quality_profile)
        self.quality_profile = self.profile.name
        if self.max_iterations is None:
            self.max_iterations = self.profile.default_iterations
        self.omni_model = resolve_omni_model(self.omni_model)
        self.vl_model = resolve_vl_model(self.vl_model)
        self.review_model = resolve_vl_model(self.review_model) if self.review_model else self.vl_model
        self._base_url, self._api_key = resolve_openai_endpoint({})
        self.validate()

    @property
    def video(self) -> Path:
        return self.video_path

    @property
    def output(self) -> Path:
        return self.output_path

    def validate(self) -> None:
        assert isinstance(self.video_path, Path)
        assert isinstance(self.output_path, Path)
        assert isinstance(self.workdir, Path)
        if _URL_PATTERN.match(str(self.video_path)):
            raise ValueError("URLs are not accepted; provide one local video file")
        if not self.video_path.is_file():
            raise ValueError("video_path must be an existing regular file")
        if self.video_path.suffix.lower() not in _LOCAL_VIDEO_SUFFIXES:
            raise ValueError(f"unsupported local video extension: {self.video_path.suffix or '<none>'}")
        if self.output_path.suffix.lower() != ".pdf":
            raise ValueError("output_path must end in .pdf")
        if self.video_path == self.output_path:
            raise ValueError("input video and output PDF must differ")
        if self.output_path == self.workdir:
            raise ValueError("output_path and workdir must differ")
        if self.output_path.exists() and not self.output_path.is_file():
            raise ValueError("output_path must be a file path")
        if self.output_path.exists() and not (self.overwrite or self.resume):
            raise ValueError("output PDF already exists; use overwrite or resume")
        if self.workdir.exists() and not self.workdir.is_dir():
            raise ValueError("workdir must be a directory path")
        if self.workdir == self.video_path or self.workdir in self.video_path.parents:
            raise ValueError("workdir cannot be the input video or contain it")
        for name in ("resume", "overwrite", "no_asr", "require_asr", "dry_run"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.no_asr and self.require_asr:
            raise ValueError("no_asr and require_asr are mutually exclusive")
        if not isinstance(self.language, str) or not self.language.strip():
            raise ValueError("language is required")
        if isinstance(self.max_iterations, bool) or not isinstance(self.max_iterations, int) or self.max_iterations < 1:
            raise ValueError("max_iterations must be a positive integer")
        if self.max_iterations > MAX_ITERATIONS_LIMIT:
            raise ValueError(f"max_iterations must not exceed {MAX_ITERATIONS_LIMIT}")
        for name in ("font", "bold_font"):
            value = getattr(self, name)
            if value is not None and not value.is_file():
                raise ValueError(f"{name} must be an existing font file")

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": str(self.video_path),
            "output_path": str(self.output_path),
            "workdir": str(self.workdir),
            "language": self.language,
            "resume": self.resume,
            "overwrite": self.overwrite,
            "quality_profile": self.quality_profile,
            "quality": asdict(self.profile),
            "max_iterations": self.max_iterations,
            "omni_model": self.omni_model,
            "vl_model": self.vl_model,
            "review_model": self.review_model,
            "base_url": self._base_url,
            "font": str(self.font) if self.font else None,
            "bold_font": str(self.bold_font) if self.bold_font else None,
            "no_asr": self.no_asr,
            "require_asr": self.require_asr,
            "dry_run": self.dry_run,
        }

    def fingerprint_payload(self) -> dict[str, Any]:
        return {key: value for key, value in self.to_dict().items() if key not in FINGERPRINT_EXCLUDED_KEYS}

    def fingerprint(self) -> str:
        encoded = json.dumps(self.fingerprint_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
