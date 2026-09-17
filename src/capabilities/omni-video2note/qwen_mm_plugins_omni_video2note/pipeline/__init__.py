"""Reusable foundations for the resumable Omni Video2Note pipeline."""

from .config import PipelineConfig, QualityProfile, resolve_quality_profile
from .frame_selection import FrameSelector, SelectionConfig
from .media import MediaChunk, extract_coarse_frames, extract_frame, prepare_video_chunks, probe_video
from .rendering import RenderArtifacts, build_document_html, rasterize_pdf, render_document
from .runner import get_status, run_video2note
from .schemas import (
    AuditReport,
    DocumentDraft,
    DocumentPlan,
    FinalReport,
    FrameCandidate,
    FrameSelection,
    ProbeResult,
    ReviewReport,
    SelectionResult,
    StepSelection,
    Transcript,
    VideoUnderstanding,
)
from .state import PipelineState, file_sha256, offline_status

__all__ = [
    "AuditReport",
    "DocumentDraft",
    "DocumentPlan",
    "FinalReport",
    "FrameCandidate",
    "FrameSelection",
    "FrameSelector",
    "MediaChunk",
    "PipelineConfig",
    "PipelineState",
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
    "get_status",
    "offline_status",
    "prepare_video_chunks",
    "probe_video",
    "rasterize_pdf",
    "render_document",
    "resolve_quality_profile",
    "run_video2note",
]
