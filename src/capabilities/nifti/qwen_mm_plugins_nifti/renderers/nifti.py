"""Render local NIfTI volumes with configurable source-voxel slices."""

from __future__ import annotations

import math
from typing import Any

from shared.env import MAX_RESPONSE_BYTES

DEFAULT_MAX_VOLUMES = 20
DEFAULT_SLICE_AXIS = 2
DEFAULT_NUM_SLICES = 3
DEFAULT_PERCENTILE_LOW = 1.0
DEFAULT_PERCENTILE_HIGH = 99.0
MAX_SLICES = 20
MAX_EXACT_VOLUME_BYTES = 64 * 1024 * 1024
MAX_INTENSITY_SAMPLES = 1_000_000

# Display starting points only. NIfTI does not reliably encode modality or HU units,
# so these presets are never selected automatically.
WINDOW_PRESETS: dict[str, tuple[float, float]] = {
    "ct_brain": (40.0, 80.0),
    "ct_soft_tissue": (50.0, 400.0),
    "ct_lung": (-600.0, 1500.0),
    "ct_bone": (500.0, 2000.0),
}

_CLOSEST_PLANE_NAMES = ("sagittal", "coronal", "axial")


def _orientation_info(image, nib) -> tuple[object, str, str, float]:
    """Return source orientation, closest-canonical display orientation, and obliquity."""
    import numpy as np

    source_codes = nib.orientations.aff2axcodes(image.affine)
    source_orientation = "".join(code or "?" for code in source_codes)

    orientation = nib.orientations.io_orientation(image.affine)
    if np.isnan(orientation[:, 0]).any():
        raise ValueError("Cannot determine all three spatial axes from the NIfTI affine")
    display_affine = image.affine.dot(nib.orientations.inv_ornt_aff(orientation, image.shape))
    display_codes = nib.orientations.aff2axcodes(display_affine)
    display_orientation = "".join(code or "?" for code in display_codes)
    obliquity_degrees = float(np.max(np.rad2deg(nib.affines.obliquity(image.affine))))
    return orientation, source_orientation, display_orientation, obliquity_degrees


def _read_source_plane(
    data,
    orientation,
    source_axis: int,
    index: int,
    volume: int | None,
):
    """Read one source-voxel plane and orient its two display axes canonically."""
    import numpy as np

    selection: list[object] = [slice(None)] * 3
    selection[source_axis] = index
    if volume is not None:
        selection.append(volume)
    plane = np.asanyarray(data[tuple(selection)])

    remaining_source_axes = [axis for axis in range(3) if axis != source_axis]
    display_source_axes = sorted(
        remaining_source_axes,
        key=lambda axis: int(orientation[axis, 0]),
    )
    transpose = tuple(remaining_source_axes.index(axis) for axis in display_source_axes)
    if transpose != tuple(range(2)):
        plane = plane.transpose(transpose)
    for plane_axis, remaining_axis in enumerate(display_source_axes):
        if orientation[remaining_axis, 1] < 0:
            plane = np.flip(plane, axis=plane_axis)
    return plane


def _deduplicate(values: list[int]) -> list[int]:
    """Deduplicate integer values while retaining their requested order."""
    return list(dict.fromkeys(values))


def _uniform_interior_indices(axis_size: int, count: int) -> list[int]:
    """Resolve interior positions from a K+2 grid whose endpoints are discarded."""
    import numpy as np

    positions = np.linspace(0, axis_size - 1, count + 2)[1:-1]
    return _deduplicate([int(value) for value in np.rint(positions)])


def _resolve_slice_indices(
    axis_size: int,
    opts: dict[str, Any],
) -> tuple[str, int, list[int], list[float] | None]:
    """Validate and resolve uniform, explicit-index, or normalized-position sampling."""
    import numpy as np

    requested_indices = opts.get("slice_indices")
    requested_positions = opts.get("slice_positions")
    if requested_indices is not None and requested_positions is not None:
        raise ValueError("slice_indices and slice_positions are mutually exclusive")

    if requested_indices is not None:
        if not isinstance(requested_indices, (list, tuple)) or not requested_indices:
            raise ValueError("slice_indices must be a non-empty list")
        if len(requested_indices) > MAX_SLICES:
            raise ValueError(f"slice_indices accepts at most {MAX_SLICES} values")
        indices: list[int] = []
        for value in requested_indices:
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise ValueError("slice_indices must contain only integers")
            index = int(value)
            if not 0 <= index < axis_size:
                raise ValueError(f"NIfTI slice index {index} is outside source axis range 0..{axis_size - 1}")
            indices.append(index)
        return "indices", len(requested_indices), _deduplicate(indices), None

    if requested_positions is not None:
        if not isinstance(requested_positions, (list, tuple)) or not requested_positions:
            raise ValueError("slice_positions must be a non-empty list")
        if len(requested_positions) > MAX_SLICES:
            raise ValueError(f"slice_positions accepts at most {MAX_SLICES} values")
        positions: list[float] = []
        for value in requested_positions:
            if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
                raise ValueError("slice_positions must contain only numbers")
            position = float(value)
            if not math.isfinite(position) or not 0.0 <= position <= 1.0:
                raise ValueError("slice_positions values must be finite and between 0 and 1")
            positions.append(position)
        indices = _deduplicate([int(value) for value in np.rint(np.asarray(positions) * (axis_size - 1))])
        return "positions", len(positions), indices, positions

    count = opts.get("num_slices", DEFAULT_NUM_SLICES)
    if isinstance(count, bool) or not isinstance(count, (int, np.integer)):
        raise ValueError("num_slices must be an integer")
    count = int(count)
    if not 1 <= count <= MAX_SLICES:
        raise ValueError(f"num_slices must be between 1 and {MAX_SLICES}")
    indices = _uniform_interior_indices(axis_size, count)
    return "uniform_interior", count, indices, None


def _sample_stride(spatial_shape: tuple[int, int, int]) -> int:
    """Return an isotropic stride whose regular grid has at most the sample cap."""
    stride = max(
        1,
        int(math.ceil((math.prod(spatial_shape) / MAX_INTENSITY_SAMPLES) ** (1 / 3))),
    )
    while math.prod(math.ceil(size / stride) for size in spatial_shape) > MAX_INTENSITY_SAMPLES:
        stride += 1
    return stride


def _parse_volumes(volumes: str, total_volumes: int) -> list[int]:
    """Parse a strict 1-based 4D volume range into unique 0-based indices."""
    if not isinstance(volumes, str) or not volumes.strip():
        raise ValueError("NIfTI volumes must be a non-empty 1-based range")

    result: list[int] = []
    for raw_part in volumes.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(f"Invalid NIfTI volumes range: {volumes!r}")
        try:
            if "-" in part:
                if part.count("-") != 1:
                    raise ValueError
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text.strip()), int(end_text.strip())
                if start > end:
                    raise ValueError
                requested = range(start, end + 1)
            else:
                requested = (int(part),)
        except ValueError:
            raise ValueError(
                f"Invalid NIfTI volumes range {volumes!r}; use values such as '1', '1-3', or '1,3'"
            ) from None

        for number in requested:
            if not 1 <= number <= total_volumes:
                raise ValueError(f"NIfTI volume number {number} is outside 1..{total_volumes}")
            result.append(number - 1)
    return _deduplicate(result)


def _read_volume_statistics_source(
    data,
    spatial_shape: tuple[int, int, int],
    source_dtype,
    volume: int | None,
):
    """Read a small volume exactly or one deterministic grid sample from a large one."""
    import numpy as np

    voxel_count = math.prod(spatial_shape)
    materialized_dtype = np.dtype(source_dtype)
    slope = getattr(data, "slope", 1.0)
    intercept = getattr(data, "inter", 0.0)
    if slope not in (None, 1.0) or intercept not in (None, 0.0):
        materialized_dtype = np.result_type(materialized_dtype, np.float64)
    # Exact percentiles need the decoded array, a finite-value copy, and a mask.
    estimated_working_bytes = voxel_count * (2 * materialized_dtype.itemsize + 1)
    exact = estimated_working_bytes <= MAX_EXACT_VOLUME_BYTES
    stride = 1 if exact else _sample_stride(spatial_shape)
    selection: list[object] = [
        (slice(None) if exact else slice(min(stride // 2, axis_size - 1), None, stride)) for axis_size in spatial_shape
    ]
    if volume is not None:
        selection.append(volume)
    sampled = np.asanyarray(data[tuple(selection)])
    full_volume = sampled if exact else None

    values = np.abs(sampled) if np.iscomplexobj(sampled) else sampled
    values = np.asanyarray(values)
    finite_values = values[np.isfinite(values)]
    return (
        full_volume,
        finite_values,
        {
            "method": "exact" if exact else "sampled",
            "sample_count": int(finite_values.size),
            "grid_count": int(values.size),
            "voxel_count": int(voxel_count),
            "stride": (stride, stride, stride),
            "observed_min": (float(np.min(finite_values)) if finite_values.size else None),
            "observed_max": (float(np.max(finite_values)) if finite_values.size else None),
        },
    )


def _finite_number(name: str, value: Any) -> float:
    """Return a finite float or raise an actionable option error."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _resolve_intensity(
    finite_values,
    statistics: dict[str, Any],
    opts: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one shared display range for a selected 3D volume."""
    import numpy as np

    mode = opts.get("intensity_mode", "auto_volume")
    if mode not in ("auto_volume", "window", "preset"):
        raise ValueError("intensity_mode must be 'auto_volume', 'window', or 'preset'")

    config: dict[str, Any] = {"mode": mode, **statistics}
    if mode == "auto_volume":
        if opts.get("window_center") is not None or opts.get("window_width") is not None:
            raise ValueError("window_center/width require intensity_mode='window'")
        if opts.get("window_preset") is not None:
            raise ValueError("window_preset requires intensity_mode='preset'")
        percentile_low = _finite_number(
            "percentile_low",
            opts.get("percentile_low", DEFAULT_PERCENTILE_LOW),
        )
        percentile_high = _finite_number(
            "percentile_high",
            opts.get("percentile_high", DEFAULT_PERCENTILE_HIGH),
        )
        if not 0.0 <= percentile_low < percentile_high <= 100.0:
            raise ValueError("NIfTI percentiles must satisfy 0 <= low < high <= 100")
        if finite_values.size:
            low, high = np.percentile(
                finite_values,
                (percentile_low, percentile_high),
                # Boolean indexing created a disposable copy: reuse it for partitioning.
                overwrite_input=True,
            )
            low, high = float(low), float(high)
        else:
            low, high = 0.0, 1.0
            config["fallback"] = "no finite voxels"
        config.update(
            {
                "percentiles": (percentile_low, percentile_high),
                "low": low,
                "high": high,
            }
        )
        return config

    if mode == "window":
        if opts.get("window_preset") is not None:
            raise ValueError("window_preset cannot be combined with intensity_mode='window'")
        center_value = opts.get("window_center")
        width_value = opts.get("window_width")
        if center_value is None or width_value is None:
            raise ValueError("intensity_mode='window' requires both window_center and window_width")
        center = _finite_number("window_center", center_value)
        width = _finite_number("window_width", width_value)
        if width <= 0:
            raise ValueError("window_width must be greater than 0")
        config.update(
            {
                "center": center,
                "width": width,
                "low": center - width / 2.0,
                "high": center + width / 2.0,
            }
        )
        return config

    preset = opts.get("window_preset")
    if preset not in WINDOW_PRESETS:
        supported = ", ".join(WINDOW_PRESETS)
        raise ValueError(f"intensity_mode='preset' requires window_preset to be one of: {supported}")
    if opts.get("window_center") is not None or opts.get("window_width") is not None:
        raise ValueError("window_center/width cannot be combined with intensity_mode='preset'")
    center, width = WINDOW_PRESETS[preset]
    config.update(
        {
            "preset": preset,
            "center": center,
            "width": width,
            "low": center - width / 2.0,
            "high": center + width / 2.0,
        }
    )
    return config


def _to_grayscale_image(values, low: float, high: float):
    """Map a numeric 2D slice through a precomputed volume-level display range."""
    import numpy as np
    from PIL import Image

    array = np.asanyarray(values)
    if array.ndim != 2:
        raise ValueError(f"Expected a 2D NIfTI slice, got shape {array.shape}")
    if np.iscomplexobj(array):
        array = np.abs(array)
    array = np.asarray(array, dtype=np.float64)

    # Rotate canonical in-plane axes into conventional screen coordinates.
    array = np.rot90(array)
    finite = np.isfinite(array)
    pixels = np.zeros(array.shape, dtype=np.uint8)
    if finite.any() and high > low:
        scaled = np.clip((array[finite] - low) / (high - low), 0.0, 1.0)
        pixels[finite] = np.rint(scaled * 255.0).astype(np.uint8)

    return Image.fromarray(pixels)


def _format_number(value: float | None) -> str:
    """Format metadata values compactly without hiding useful precision."""
    return "n/a" if value is None else f"{value:.6g}"


def _metadata_text(
    image,
    orientation,
    source_orientation: str,
    display_orientation: str,
    obliquity_degrees: float,
    source_axis: int,
    sampling_mode: str,
    requested_count: int,
    indices: list[int],
    positions: list[float] | None,
    volume_indices: list[int] | None = None,
    defaulted_4d: bool = False,
    volume_selection_truncated: bool = False,
) -> str:
    import numpy as np

    shape = tuple(int(size) for size in image.shape)
    spacing = tuple(float(value) for value in image.header.get_zooms()[:3])
    header_space_unit = image.header.get_xyzt_units()[0]
    if header_space_unit == "unknown":
        space_unit = "mm (default; NIfTI header unit is unknown)"
    else:
        space_unit = header_space_unit
    affine = np.array2string(
        np.asarray(image.affine),
        precision=6,
        suppress_small=True,
    )
    closest_world_axis = int(orientation[source_axis, 0])
    plane_name = _CLOSEST_PLANE_NAMES[closest_world_axis]
    direction_code = source_orientation[source_axis]

    lines = [
        "**NIfTI volume**",
        f"- Shape: {shape}",
        f"- Dtype: {image.get_data_dtype()}",
        f"- Voxel spacing: {spacing} {space_unit}",
        (
            "- Orientation (closest axis codes): closest-canonical reference "
            f"{display_orientation}; source {source_orientation}"
        ),
        f"- Maximum voxel-axis obliquity: {obliquity_degrees:.3f} degrees",
    ]
    if volume_indices:
        volume_numbers = tuple(index + 1 for index in volume_indices)
        indices_4d = tuple(volume_indices)
        if len(volume_indices) == 1:
            lines.append(f"- Selected 3D volume: number {volume_numbers[0]} / index {indices_4d[0]} (of {shape[3]})")
        else:
            lines.append(f"- Selected 3D volumes: numbers {volume_numbers}; indices {indices_4d} (of {shape[3]})")
        if defaulted_4d:
            lines.append("- 4D handling: defaulted to the first 3D volume; remaining volumes were not rendered")
        fourth_spacing = float(image.header.get_zooms()[3])
        fourth_unit = image.header.get_xyzt_units()[1]
        lines.append(f"- Fourth-dimension spacing: {fourth_spacing:g} {fourth_unit}")
        if volume_selection_truncated:
            lines.append("- Volume selection was truncated by max_volumes")
    lines.extend(
        (
            (
                f"- Sampling: source voxel axis {source_axis} "
                f"(closest {plane_name} plane; increasing toward {direction_code}); "
                "no resampling"
            ),
            f"- Slice selection mode: {sampling_mode}",
            (f"- Slice count: requested {requested_count}; resolved {len(indices)}"),
            f"- Resolved source indices (0-based): {tuple(indices)}",
        )
    )
    if positions is not None:
        lines.append(f"- Requested normalized positions: {tuple(positions)}")
    lines.append(f"- Source affine:\n```text\n{affine}\n```")
    return "\n".join(lines)


def _intensity_text(
    config: dict[str, Any],
    slice_count: int,
    returned_count: int | None = None,
) -> str:
    """Return the effective intensity configuration for user/model verification."""
    lines = ["**Effective NIfTI intensity configuration**"]
    mode = config["mode"]
    lines.append(f"- Intensity mode: {mode}")
    if mode == "auto_volume":
        percentile_low, percentile_high = config["percentiles"]
        lines.append(f"- Percentiles requested: {percentile_low:g}–{percentile_high:g}")
    elif mode == "preset":
        lines.append(f"- Window preset: {config['preset']}")
        lines.append(f"- Window center / width: {_format_number(config['center'])} / {_format_number(config['width'])}")
    else:
        lines.append(f"- Window center / width: {_format_number(config['center'])} / {_format_number(config['width'])}")
    lines.extend(
        (
            (
                "- Statistics: exact"
                if config["method"] == "exact"
                else "- Statistics: sampled (deterministic regular grid)"
            ),
            (
                f"- Finite sample voxels: {config['sample_count']} / "
                f"{config['voxel_count']}; grid stride {config['stride']}"
            ),
            (
                "- Observed sample range: "
                f"[{_format_number(config['observed_min'])}, "
                f"{_format_number(config['observed_max'])}]"
            ),
            (f"- Effective display range: [{_format_number(config['low'])}, {_format_number(config['high'])}]"),
            f"- Configured consistently for: {slice_count} resolved slices",
        )
    )
    if returned_count is not None and returned_count < slice_count:
        lines.append(f"- Returned before response-size truncation: {returned_count} / {slice_count} slices")
    if config.get("fallback"):
        lines.append(f"- Fallback: {config['fallback']}")
    if config["high"] <= config["low"]:
        lines.append("- Degenerate range: rendered finite pixels are black")
    return "\n".join(lines)


def _block_size(block: dict[str, Any]) -> int:
    """Estimate serialized response bytes from a text or base64 image block."""
    if block.get("type") == "image":
        return len(block.get("data", ""))
    return len(block.get("text", "").encode("utf-8"))


def labeled_image(label: str, image, budget: str = "large") -> list[dict[str, Any]]:
    """Pair a slice label and effective display dimensions with its image."""
    from shared.image import render_image_block

    block, width, height = render_image_block(image, budget)
    return [{"type": "text", "text": f"{label} {height}x{width} (HxW)"}, block]


def render(path: str, **opts: Any) -> list[dict[str, Any]]:
    """Read a 3D/4D NIfTI file and render configured source-voxel slices."""
    try:
        import nibabel as nib
    except ImportError:
        raise RuntimeError('Missing dependency — install with: pip install "qwen-mm-plugins[nifti]"')

    import numpy as np

    source = nib.load(path)
    if source.ndim not in (3, 4):
        raise ValueError(f"NIfTI visualization supports 3D or 4D images, got {source.ndim}D shape {source.shape}")
    if any(size < 1 for size in source.shape):
        raise ValueError(f"NIfTI image has an empty dimension: {source.shape}")

    source_axis_value = opts.get("slice_axis", DEFAULT_SLICE_AXIS)
    if isinstance(source_axis_value, bool) or not isinstance(source_axis_value, (int, np.integer)):
        raise ValueError("slice_axis must be 0, 1, or 2")
    source_axis = int(source_axis_value)
    if source_axis not in (0, 1, 2):
        raise ValueError("slice_axis must be 0, 1, or 2")

    sampling_mode, requested_count, slice_indices, positions = _resolve_slice_indices(
        int(source.shape[source_axis]), opts
    )
    orientation, source_orientation, display_orientation, obliquity = _orientation_info(source, nib)

    volumes = opts.get("volumes")
    defaulted_4d = source.ndim == 4 and volumes is None
    volume_selection_truncated = False
    if source.ndim == 4:
        max_volumes = opts.get("max_volumes", DEFAULT_MAX_VOLUMES)
        if isinstance(max_volumes, bool) or not isinstance(max_volumes, (int, np.integer)):
            raise ValueError("max_volumes must be a positive integer")
        max_volumes = int(max_volumes)
        if max_volumes < 1:
            raise ValueError("max_volumes must be a positive integer")
        requested_volumes = _parse_volumes(volumes, int(source.shape[3])) if volumes is not None else [0]
        volume_selection_truncated = len(requested_volumes) > max_volumes
        volume_indices: list[int | None] = requested_volumes[:max_volumes]
    else:
        if volumes is not None:
            _parse_volumes(volumes, 1)
        volume_indices = [None]

    result: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _metadata_text(
                source,
                orientation,
                source_orientation,
                display_orientation,
                obliquity,
                source_axis,
                sampling_mode,
                requested_count,
                slice_indices,
                positions,
                [index for index in volume_indices if index is not None],
                defaulted_4d,
                volume_selection_truncated,
            ),
        }
    ]

    budget = opts.get("budget", "large")
    response_bytes = sum(_block_size(block) for block in result)
    rendered_images = 0
    rendered_volumes = 0
    spatial_shape = tuple(int(size) for size in source.shape[:3])
    source_dtype = source.get_data_dtype()
    for volume_index in volume_indices:
        full_volume, finite_values, statistics = _read_volume_statistics_source(
            source.dataobj,
            spatial_shape,
            source_dtype,
            volume_index,
        )
        intensity = _resolve_intensity(finite_values, statistics, opts)
        pending_blocks: list[dict[str, Any]] = []
        if volume_index is not None:
            pending_blocks.append(
                {
                    "type": "text",
                    "text": (f"**Volume number {volume_index + 1} / index {volume_index}**"),
                }
            )
        intensity_block = {
            "type": "text",
            "text": _intensity_text(intensity, len(slice_indices)),
        }
        pending_blocks.append(intensity_block)
        prefix_added = False
        rendered_for_volume = 0
        plane_source = full_volume if full_volume is not None else source.dataobj
        plane_volume = None if full_volume is not None else volume_index
        for index in slice_indices:
            values = _read_source_plane(
                plane_source,
                orientation,
                source_axis,
                index,
                plane_volume,
            )
            image = _to_grayscale_image(
                values,
                intensity["low"],
                intensity["high"],
            )
            image_blocks = labeled_image(
                f"Source axis {source_axis} slice (index={index})",
                image,
                budget,
            )
            candidate_blocks = [*pending_blocks, *image_blocks] if not prefix_added else image_blocks
            candidate_bytes = sum(_block_size(block) for block in candidate_blocks)
            if rendered_images and response_bytes + candidate_bytes > MAX_RESPONSE_BYTES:
                if prefix_added:
                    intensity_block["text"] = _intensity_text(
                        intensity,
                        len(slice_indices),
                        rendered_for_volume,
                    )
                stopped_at = (
                    f"volume number {volume_index + 1}, source index {index}"
                    if volume_index is not None
                    else f"source index {index}"
                )
                slice_word = "slice" if rendered_images == 1 else "slices"
                volume_word = "volume" if rendered_volumes == 1 else "volumes"
                rendered_summary = f"{rendered_images} {slice_word} across {rendered_volumes} {volume_word}"
                result[0]["text"] += f"\n- Response-size truncation: returned {rendered_summary}"
                result.append(
                    {
                        "type": "text",
                        "text": (
                            f"NIfTI output truncated at {rendered_summary} "
                            f"before {stopped_at} to stay below the response size limit; "
                            "request fewer volumes or slices to inspect the remainder."
                        ),
                    }
                )
                return result
            if not prefix_added:
                result.extend(pending_blocks)
                prefix_added = True
                rendered_volumes += 1
            result.extend(image_blocks)
            response_bytes += candidate_bytes
            rendered_images += 1
            rendered_for_volume += 1
    return result
