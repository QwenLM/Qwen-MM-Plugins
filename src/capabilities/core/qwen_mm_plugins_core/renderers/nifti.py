"""Render local NIfTI volumes as three orthogonal center slices plus metadata."""

from __future__ import annotations

from typing import Any


def _orientation_info(image, nib) -> tuple[object, str, str]:
    """Return the lazy source-to-canonical transform and orientation labels."""
    import numpy as np

    source_codes = nib.orientations.aff2axcodes(image.affine)
    source_orientation = "".join(code or "?" for code in source_codes)

    orientation = nib.orientations.io_orientation(image.affine)
    if np.isnan(orientation[:, 0]).any():
        raise ValueError("Cannot determine all three spatial axes from the NIfTI affine")
    display_affine = image.affine.dot(nib.orientations.inv_ornt_aff(orientation, image.shape))
    display_codes = nib.orientations.aff2axcodes(display_affine)
    display_orientation = "".join(code or "?" for code in display_codes)
    return orientation, source_orientation, display_orientation


def _canonical_shape(source_shape: tuple[int, ...], orientation) -> tuple[int, int, int]:
    """Return spatial dimensions ordered by canonical RAS axes."""
    source_axes = sorted(range(3), key=lambda axis: int(orientation[axis, 0]))
    return tuple(int(source_shape[axis]) for axis in source_axes)


def _read_canonical_plane(data, source_shape, orientation, canonical_axis: int, index: int, volume: int | None):
    """Read one canonical 2D plane directly from the source array proxy."""
    import numpy as np

    source_axes = sorted(range(3), key=lambda axis: int(orientation[axis, 0]))
    fixed_source_axis = source_axes[canonical_axis]
    source_index = index
    if orientation[fixed_source_axis, 1] < 0:
        source_index = int(source_shape[fixed_source_axis]) - 1 - index

    selection: list[object] = [slice(None)] * 3
    selection[fixed_source_axis] = source_index
    if volume is not None:
        selection.append(volume)
    plane = np.asanyarray(data[tuple(selection)])

    remaining_source_axes = [axis for axis in range(3) if axis != fixed_source_axis]
    canonical_source_axes = [axis for axis in source_axes if axis != fixed_source_axis]
    transpose = tuple(remaining_source_axes.index(axis) for axis in canonical_source_axes)
    if transpose != tuple(range(2)):
        plane = plane.transpose(transpose)
    for plane_axis, source_axis in enumerate(canonical_source_axes):
        if orientation[source_axis, 1] < 0:
            plane = np.flip(plane, axis=plane_axis)
    return plane


def _to_grayscale_image(values):
    """Map a numeric 2D slice to a robust 8-bit grayscale PIL image."""
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
    if finite.any():
        finite_values = array[finite]
        low, high = np.percentile(finite_values, (1.0, 99.0))
        if high > low:
            scaled = np.clip((array[finite] - low) / (high - low), 0.0, 1.0)
            pixels[finite] = np.rint(scaled * 255.0).astype(np.uint8)

    return Image.fromarray(pixels)


def _metadata_text(
    image,
    source_orientation: str,
    display_orientation: str,
    volume_indices: list[int] | None = None,
) -> str:
    import numpy as np

    shape = tuple(int(size) for size in image.shape)
    spacing = tuple(float(value) for value in image.header.get_zooms()[:3])
    affine = np.array2string(
        np.asarray(image.affine),
        precision=6,
        suppress_small=True,
    )

    lines = [
        "**NIfTI volume**",
        f"- Shape: {shape}",
        f"- Dtype: {image.get_data_dtype()}",
        f"- Voxel spacing: {spacing} mm",
        f"- Orientation (closest axis codes): display {display_orientation}; source {source_orientation}",
    ]
    if volume_indices:
        pages = tuple(index + 1 for index in volume_indices)
        indices = tuple(volume_indices)
        if len(volume_indices) == 1:
            lines.append(f"- Selected volume: page {pages[0]} / index {indices[0]} (of {shape[3]})")
        else:
            lines.append(f"- Selected volumes: pages {pages}; indices {indices} (of {shape[3]})")
    lines.extend(
        (
            "- Display: axial, coronal, and sagittal center voxel planes (no resampling)",
            f"- Source affine:\n```text\n{affine}\n```",
        )
    )
    return "\n".join(lines)


def render(path: str, **opts: Any) -> list[dict[str, Any]]:
    """Read a 3D/4D NIfTI file without modifying it and render center slices."""
    try:
        import nibabel as nib
    except ImportError:
        raise RuntimeError('Missing dependency — install with: pip install "qwen-mm-plugins[viz]"')

    from qwen_mm_plugins_core.renderers import DEFAULT_MAX_PAGES, labeled_image, parse_pages

    source = nib.load(path)
    if source.ndim not in (3, 4):
        raise ValueError(f"NIfTI visualization supports 3D or 4D images, got {source.ndim}D shape {source.shape}")
    if any(size < 1 for size in source.shape):
        raise ValueError(f"NIfTI image has an empty dimension: {source.shape}")

    orientation, source_orientation, display_orientation = _orientation_info(source, nib)
    display_shape = _canonical_shape(source.shape, orientation)
    x_center, y_center, z_center = (size // 2 for size in display_shape)
    if source.ndim == 4:
        pages = opts.get("pages")
        volume_indices: list[int | None] = (
            parse_pages(pages, int(source.shape[3]))[: opts.get("max_pages", DEFAULT_MAX_PAGES)] if pages else [0]
        )
    else:
        volume_indices = [None]
    planes = (
        ("Axial", 2, z_center, f"z={z_center}"),
        ("Coronal", 1, y_center, f"y={y_center}"),
        ("Sagittal", 0, x_center, f"x={x_center}"),
    )

    result: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _metadata_text(
                source,
                source_orientation,
                display_orientation,
                [index for index in volume_indices if index is not None],
            ),
        }
    ]
    budget = opts.get("budget", "large")
    for volume_index in volume_indices:
        if volume_index is not None:
            result.append(
                {
                    "type": "text",
                    "text": f"**Volume page {volume_index + 1} / index {volume_index}**",
                }
            )
        for plane, canonical_axis, index, index_label in planes:
            values = _read_canonical_plane(
                source.dataobj,
                source.shape,
                orientation,
                canonical_axis,
                index,
                volume_index,
            )
            image = _to_grayscale_image(values)
            result.extend(labeled_image(f"{plane} center slice ({index_label})", image, budget))
    return result
