"""Reusable foundations for the Omni Video2Note pipeline."""

from .artifacts import file_sha256
from .config import PipelineConfig, QualityProfile, resolve_quality_profile
from .frame_selection import FrameSelector, SelectionConfig
from .media import MediaChunk, extract_coarse_frames, extract_frame, prepare_video_chunks, probe_video
from .rendering import RenderArtifacts, build_document_html, rasterize_pdf, render_document
from .runner import run_video2note
from .schemas import (
    AuditReport,
    DocumentDraft,
    DocumentPlan,
    FrameCandidate,
    FrameSelection,
    ProbeResult,
    ReviewReport,
    SelectionResult,
    StepSelection,
    Transcript,
    VideoUnderstanding,
)

__all__ = [
    "AuditReport",
    "DocumentDraft",
    "DocumentPlan",
    "FrameCandidate",
    "FrameSelection",
    "FrameSelector",
    "MediaChunk",
    "PipelineConfig",
    "ProbeResult",
    "QualityProfile",
    "RenderArtifacts",
    "ReviewReport",
    "SelectionConfig",
    "SelectionResult",
    "StepSelection",
    "Transcript",
    "VideoUnderstanding",
    "build_document_html",
    "extract_coarse_frames",
    "extract_frame",
    "file_sha256",
    "prepare_video_chunks",
    "probe_video",
    "rasterize_pdf",
    "render_document",
    "resolve_quality_profile",
    "run_video2note",
]
