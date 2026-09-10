"""MCP entry point for configurable local NIfTI visualization."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from shared.content import require_file, text_error

NormalizedPosition = Annotated[float, Field(ge=0.0, le=1.0, strict=True, allow_inf_nan=False)]
SliceIndex = Annotated[int, Field(ge=0, strict=True)]


class NiftiVisualizeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    # FastMCP reconstructs a model from field annotations, so keep wire validation on each field.
    file_path: str = Field(min_length=1, strict=True)
    volumes: str | None = Field(default=None, min_length=1, strict=True)
    max_volumes: int = Field(default=20, ge=1, strict=True)
    budget: Literal["small", "normal", "large"] = "large"
    slice_axis: int = Field(default=2, ge=0, le=2, strict=True)
    num_slices: int = Field(default=3, ge=1, le=20, strict=True)
    slice_indices: list[SliceIndex] | None = Field(default=None, min_length=1, max_length=20, strict=True)
    slice_positions: list[NormalizedPosition] | None = Field(default=None, min_length=1, max_length=20, strict=True)
    intensity_mode: Literal["auto_volume", "window", "preset"] = "auto_volume"
    percentile_low: float = Field(default=1.0, ge=0.0, le=100.0, strict=True, allow_inf_nan=False)
    percentile_high: float = Field(default=99.0, ge=0.0, le=100.0, strict=True, allow_inf_nan=False)
    window_center: float | None = Field(default=None, strict=True, allow_inf_nan=False)
    window_width: float | None = Field(default=None, gt=0.0, strict=True, allow_inf_nan=False)
    window_preset: Literal["ct_brain", "ct_soft_tissue", "ct_lung", "ct_bone"] | None = None

    @model_validator(mode="after")
    def validate_options(self) -> NiftiVisualizeArgs:
        if self.volumes is not None and not self.volumes.strip():
            raise ValueError("volumes must be a non-empty 1-based range")
        if self.slice_indices is not None and self.slice_positions is not None:
            raise ValueError("slice_indices and slice_positions are mutually exclusive")
        if self.percentile_low >= self.percentile_high:
            raise ValueError("percentiles must satisfy 0 <= low < high <= 100")
        has_window = self.window_center is not None or self.window_width is not None
        if self.intensity_mode == "window":
            if self.window_center is None or self.window_width is None:
                raise ValueError("intensity_mode='window' requires both window_center and window_width")
            if self.window_preset is not None:
                raise ValueError("window_preset cannot be combined with intensity_mode='window'")
        elif self.intensity_mode == "preset":
            if self.window_preset is None:
                raise ValueError("intensity_mode='preset' requires window_preset")
            if has_window:
                raise ValueError("window_center/width cannot be combined with intensity_mode='preset'")
        else:
            if has_window:
                raise ValueError("window_center/width require intensity_mode='window'")
            if self.window_preset is not None:
                raise ValueError("window_preset requires intensity_mode='preset'")
        return self


TOOL = {"name": "nifti_visualize", "args": NiftiVisualizeArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Visualize a local 3D or 4D NIfTI with configurable source-voxel slices and intensity mapping.

    Prefer this specialized tool for NIfTI inspection when it is available. By default, render
    three uniformly spaced interior slices along source voxel axis 2, sharing one volume-level
    P1-P99 range. Source axes need not align with anatomical axes; slices are oriented for
    display without anatomical resampling. A 4D input defaults to its first 3D volume.

    Return grayscale images and effective settings, including source indices, selected volumes,
    exact or sampled statistics, intensity limits, orientation, and any output truncation.
    Header spatial units are respected; unknown units are explicitly interpreted as mm.
    CT presets are optional display starting points, not modality detection or diagnosis.
    The input file is never modified.

    Args:
        file_path: Absolute path to a local .nii or .nii.gz file. URLs are not supported.
        volumes: Optional 1-based 4D volume selection, such as '1', '1-3', or '1,3'. Defaults
            to the first volume. A 3D file has only volume 1. Invalid selections return an error.
        max_volumes: Maximum selected 3D volumes to render, default 20. This caps volumes,
            not slices; the response-size limit may truncate output earlier.
        budget: Resolution preset per slice: small (~512x512), normal (~1024x1024), or
            large (~1448x1448). Actual dimensions depend on the source aspect ratio.
        slice_axis: Source voxel-array axis 0, 1, or 2; default 2. This is not necessarily an
            anatomical axis, especially for oblique images.
        num_slices: Number of uniform interior slices, from 1 to 20; default 3. Choose positions
            on a K+2 grid and discard its endpoints. Ignored when explicit indices or positions
            are supplied; duplicate resolved indices on short axes are rendered only once.
        slice_indices: Explicit 0-based indices along slice_axis, at most 20, in requested
            order. Mutually exclusive with slice_positions. Duplicate indices are deduplicated.
        slice_positions: Up to 20 normalized source-axis positions in [0, 1], where 0 and 1
            are the endpoints. Positions map to the nearest index; duplicate indices are
            deduplicated. Mutually exclusive with slice_indices.
        intensity_mode: Shared display mapping for each selected 3D volume: auto_volume uses
            volume-level percentiles, window uses a custom center/width, and preset uses an
            explicitly named CT window. Default auto_volume. Large volumes use a deterministic
            regular-grid percentile sample whose method and limits are returned.
        percentile_low: Lower percentile in auto_volume mode, default 1. Must be at least 0
            and less than percentile_high.
        percentile_high: Upper percentile in auto_volume mode, default 99. Must be greater
            than percentile_low and at most 100.
        window_center: Custom window center in scaled voxel-value units. Required together
            with window_width in window mode; cannot be combined with other intensity modes.
        window_width: Positive custom window width in scaled voxel-value units. Window mode
            maps center-width/2 to black and center+width/2 to white, clipping outside values.
        window_preset: Explicit preset required in preset mode: ct_brain (center 40, width 80),
            ct_soft_tissue (50, 400), ct_lung (-600, 1500), or ct_bone (500, 2000). These assume
            HU-like CT intensities; no preset is selected automatically. Cannot be combined
            with a custom center/width.
    """
    try:
        options = NiftiVisualizeArgs.model_validate(arguments).model_dump()
    except ValidationError as exc:
        return text_error(f"Invalid NIfTI options: {exc}")

    path = options.pop("file_path")
    if "://" in path or not Path(path).is_absolute():
        return text_error("file_path must be an absolute local .nii or .nii.gz path; URLs are not supported")
    if not path.lower().endswith((".nii", ".nii.gz")):
        return text_error("unsupported file type; nifti_visualize accepts local .nii and .nii.gz files")
    if error := require_file(path):
        return error

    from qwen_mm_plugins_nifti.renderers.nifti import render

    try:
        return render(path, **options)
    except ImportError:
        return text_error('Missing dependency. Install with: pip install "qwen-mm-plugins[nifti]"')
    except Exception as exc:
        return text_error(f"Error rendering NIfTI file: {exc}")
