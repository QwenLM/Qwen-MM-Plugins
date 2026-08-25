"""MCP tool: visualize any supported file by extension dispatch."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_core.renderers import (
    DEFAULT_MAX_PAGES,
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    VIDEO_EXTENSIONS,
    detect_extension,
    get_renderer,
)
from shared.content import require_file, text_error

_SORTED_EXTS = sorted(SUPPORTED_EXTENSIONS)
NormalizedNiftiPosition = Annotated[float, Field(ge=0.0, le=1.0)]


class VisualizeArgs(BaseModel):
    file_path: str = Field(description="Absolute path to the file to visualize.")
    pages: Optional[str] = Field(
        default=None,
        description=(
            "1-based page range for multi-page documents (e.g. '1-5', '3', "
            "'1,3,5-8'). Documents default to their first 20 pages. For 4D "
            "NIfTI, selects volumes and defaults to volume 1."
        ),
    )
    budget: Literal["small", "normal", "large"] = Field(
        default="large",
        description=("Resolution preset per page/image: small (~512x512), normal (~1024x1024), large (~1448x1448)."),
    )
    max_pages: int = Field(
        default=20,
        description=(
            "Maximum pages to render (default 20). For 4D NIfTI, caps selected volumes rather than slices per volume."
        ),
    )
    nifti_slice_axis: int = Field(
        default=2,
        ge=0,
        le=2,
        description=(
            "NIfTI only: source voxel-array axis to slice (0, 1, or 2); "
            "default 2. This is not necessarily an anatomical axis."
        ),
    )
    nifti_num_slices: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "NIfTI only: number of uniformly spaced interior slices; default 3. "
            "Ignored when nifti_slice_indices or nifti_slice_positions is set."
        ),
    )
    nifti_slice_indices: Optional[list[int]] = Field(
        default=None,
        min_length=1,
        max_length=20,
        description=("NIfTI only: explicit 0-based source-axis slice indices (maximum 20)."),
    )
    nifti_slice_positions: Optional[list[NormalizedNiftiPosition]] = Field(
        default=None,
        min_length=1,
        max_length=20,
        description=(
            "NIfTI only: normalized source-axis positions from 0.0 to 1.0 "
            "(maximum 20); mutually exclusive with nifti_slice_indices."
        ),
    )
    nifti_intensity_mode: Literal["auto_volume", "window", "preset"] = Field(
        default="auto_volume",
        description=(
            "NIfTI only: one shared intensity mapping per selected 3D volume. "
            "auto_volume uses volume-wide percentiles; window uses an explicit "
            "center/width; preset uses a named CT display preset."
        ),
    )
    nifti_percentile_low: float = Field(
        default=1.0,
        ge=0.0,
        le=100.0,
        description="NIfTI auto_volume lower percentile; default 1.",
    )
    nifti_percentile_high: float = Field(
        default=99.0,
        ge=0.0,
        le=100.0,
        description="NIfTI auto_volume upper percentile; default 99.",
    )
    nifti_window_center: Optional[float] = Field(
        default=None,
        description="NIfTI window mode center in scaled voxel-value units.",
    )
    nifti_window_width: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="NIfTI window mode width in scaled voxel-value units.",
    )
    nifti_window_preset: Optional[Literal["ct_brain", "ct_soft_tissue", "ct_lung", "ct_bone"]] = Field(
        default=None,
        description=(
            "NIfTI preset mode name. CT presets are display starting points, "
            "not modality detection or diagnostic settings."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "visualize",
    "description": (
        "Visualize any supported file for model consumption. "
        "Renders documents (PDF, DOCX, PPTX, XLSX, CSV), "
        "code files (syntax-highlighted), SVG, DrawIO diagrams, "
        "subtitles (SRT/VTT), NIfTI medical volumes, images, and videos as visual output. "
        "Automatically detects file type and applies the appropriate renderer."
    ),
    "args": VisualizeArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    file_path = arguments.get("file_path", "")

    # URLs go to the web renderer.
    if file_path.startswith(("http://", "https://")):
        from qwen_mm_plugins_core.renderers.web import render as web_render

        return web_render(
            file_path,
            pages=arguments.get("pages"),
            max_pages=arguments.get("max_pages", DEFAULT_MAX_PAGES),
            budget=arguments.get("budget", "large"),
        )

    if err := require_file(file_path):
        return err

    ext = detect_extension(file_path)
    if not ext:
        return text_error(f"cannot determine file type (no extension): {file_path}")

    if ext not in SUPPORTED_EXTENSIONS:
        return text_error(f"unsupported file type '{ext}'. Supported: {', '.join(_SORTED_EXTS)}")

    if ext in IMAGE_EXTENSIONS:
        from qwen_mm_plugins_core.readers.image import handle as image_handle

        return image_handle(
            {
                "image_path": file_path,
                "budget": arguments.get("budget", "large"),
            }
        )

    if ext in VIDEO_EXTENSIONS:
        from qwen_mm_plugins_core.readers.video import handle as video_handle

        return video_handle(
            {
                "video_path": file_path,
                "budget": arguments.get("budget", "large"),
            }
        )

    renderer = get_renderer(ext)
    if renderer is None:
        return text_error(f"no renderer available for '{ext}'")

    renderer_options = {
        "pages": arguments.get("pages"),
        "max_pages": arguments.get("max_pages", DEFAULT_MAX_PAGES),
        "budget": arguments.get("budget", "large"),
    }
    if ext in (".nii", ".nii.gz"):
        for name in (
            "nifti_slice_axis",
            "nifti_num_slices",
            "nifti_slice_indices",
            "nifti_slice_positions",
            "nifti_intensity_mode",
            "nifti_percentile_low",
            "nifti_percentile_high",
            "nifti_window_center",
            "nifti_window_width",
            "nifti_window_preset",
        ):
            if name in arguments:
                renderer_options[name] = arguments[name]

    try:
        result = renderer(file_path, **renderer_options)
    except Exception as e:
        return [{"type": "text", "text": f"Error rendering {ext} file: {e}"}]

    if not result:
        return [{"type": "text", "text": f"Warning: renderer returned no output for {file_path}"}]

    return result
