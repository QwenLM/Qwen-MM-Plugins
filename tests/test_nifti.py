"""Offline coverage for NIfTI visualization.

Tests use generated synthetic fixtures plus a public MNI152 population-average template; no
individual patient scan is included.
"""

import base64
import io
import math
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from qwen_mm_plugins_core.renderers import nifti
from qwen_mm_plugins_core.visualizers import visualize

nib = pytest.importorskip("nibabel")
ASSETS_DIR = Path(__file__).with_name("assets")


def _save_nifti(tmp_path, filename: str, data: np.ndarray, affine: np.ndarray | None = None) -> str:
    """Write a small deterministic test volume and return its path."""
    if affine is None:
        affine = np.diag([2.0, 3.0, 4.0, 1.0])
    path = tmp_path / filename
    nib.save(nib.Nifti1Image(data, affine), path)
    return str(path)


def _text(content: list[dict]) -> str:
    return "\n".join(block["text"] for block in content if block.get("type") == "text")


def _images(content: list[dict]) -> list[dict]:
    return [block for block in content if block.get("type") == "image"]


def _guard_array_proxy(monkeypatch) -> list[tuple]:
    """Fail on whole-array conversion and record lazy proxy slices."""
    selections = []
    proxy_cls = nib.arrayproxy.ArrayProxy
    original_getitem = proxy_cls.__getitem__

    def reject_materialization(self, *args, **kwargs):
        raise AssertionError("NIfTI renderer materialized the full volume")

    def record_slice(self, selection):
        selections.append(selection)
        return original_getitem(self, selection)

    monkeypatch.setattr(proxy_cls, "__array__", reject_materialization)
    monkeypatch.setattr(proxy_cls, "__getitem__", record_slice)
    return selections


def _assert_successful_render(content: list[dict], expected_images: int = 3) -> list[Image.Image]:
    report = _text(content)
    assert not any(line.startswith("Error") for line in report.splitlines()), report

    blocks = _images(content)
    assert len(blocks) == expected_images, f"expected {expected_images} NIfTI slices, got {len(blocks)}: {report}"

    decoded = []
    for block in blocks:
        assert block.get("mimeType", "").startswith("image/")
        image = Image.open(io.BytesIO(base64.b64decode(block["data"])))
        image.load()
        assert image.width > 0 and image.height > 0
        decoded.append(image)
    return decoded


def test_visualize_nifti_asset_reports_default_sampling_and_intensity_config():
    path = ASSETS_DIR / "avg152T1_LR_nifti.nii.gz"

    content = visualize.handle({"file_path": str(path), "budget": "small"})

    _assert_successful_render(content)
    report = _text(content)
    report_lower = report.lower()
    assert "nifti" in report_lower
    assert "shape" in report_lower and re.search(r"91\D+109\D+91", report)
    assert "dtype" in report_lower and "uint8" in report_lower
    assert ("voxel spacing" in report_lower or "zooms" in report_lower) and "2.0" in report
    assert re.search(
        r"orientation.*closest-canonical reference\s+RAS.*source\s+LAS",
        report,
        re.IGNORECASE,
    )
    assert "affine" in report_lower
    assert "source voxel axis 2" in report_lower
    assert "closest axial plane" in report_lower
    assert "uniform_interior" in report
    assert "Resolved source indices (0-based): (22, 45, 68)" in report
    assert "Intensity mode: auto_volume" in report
    assert "Percentiles requested: 1–99" in report
    assert "Configured consistently for: 3 resolved slices" in report


def test_visualize_nifti_4d_defaults_and_selects_pages_as_3d_volumes(tmp_path, monkeypatch):
    shape = (7, 9, 11)
    x, y, z = np.indices(shape, dtype=np.float32)
    first = x + 10 * y + 100 * z
    second = 2 * first
    third = 3 * first
    data = np.stack([first, second, third], axis=-1)
    path = _save_nifti(tmp_path, "synthetic-4d.nii", data)
    selections = _guard_array_proxy(monkeypatch)

    content = visualize.handle({"file_path": path, "budget": "small"})

    _assert_successful_render(content)
    report = _text(content)
    assert "Selected 3D volume: page 1 / index 0 (of 3)" in report
    assert "defaulted to the first 3D volume" in report
    assert "**Volume page 1 / index 0**" in report
    low, high = np.percentile(first, (1, 99))
    assert f"Effective display range: [{low:.6g}, {high:.6g}]" in report
    assert len(selections) == 1
    assert selections[0][-1] == 0
    assert all(isinstance(axis, slice) for axis in selections[0][:3])

    selections.clear()
    content = visualize.handle(
        {
            "file_path": path,
            "pages": "1-3",
            "max_pages": 2,
            "budget": "small",
        }
    )

    report = _text(content)
    assert len(_images(content)) == 6
    assert "Selected 3D volumes: pages (1, 2); indices (0, 1) (of 3)" in report
    assert "**Volume page 1 / index 0**" in report
    assert "**Volume page 2 / index 1**" in report
    assert "Volume page 3" not in report
    assert "Volume selection was truncated by max_pages" in report
    assert "Fourth-dimension spacing: 1 unknown" in report
    assert len(selections) == 2
    assert [selection[-1] for selection in selections] == [0, 1]
    assert all(all(isinstance(axis, slice) for axis in selection[:3]) for selection in selections)


def test_visualize_nifti_noncanonical_orientation_uses_source_axis(tmp_path, monkeypatch):
    shape = (6, 8, 10)
    x, y, z = np.indices(shape, dtype=np.float32)
    first = x + 10 * y + 100 * z
    affine = np.diag([2.0, 3.0, 4.0, 1.0])
    ras_image = nib.Nifti1Image(first, affine)
    transform = nib.orientations.ornt_transform(
        nib.orientations.axcodes2ornt(("R", "A", "S")),
        nib.orientations.axcodes2ornt(("S", "L", "A")),
    )
    source_image = ras_image.as_reoriented(transform)
    path = tmp_path / "sla-source.nii.gz"
    nib.save(source_image, path)
    selections = _guard_array_proxy(monkeypatch)

    content = visualize.handle({"file_path": str(path), "budget": "small"})

    _assert_successful_render(content)
    report = _text(content)
    assert re.search(r"orientation.*RAS.*source\s+SLA", report, re.IGNORECASE), report
    assert "source voxel axis 2 (closest coronal plane; increasing toward A)" in report
    assert "Resolved source indices (0-based): (2, 4, 5)" in report
    assert len(selections) == 1
    assert selections[0] == (slice(None), slice(None), slice(None))

    source_orientation = nib.orientations.io_orientation(source_image.affine)
    actual = nifti._read_source_plane(
        source_image.dataobj,
        source_orientation,
        source_axis=2,
        index=2,
        volume=None,
    )
    canonical = np.asanyarray(nib.as_closest_canonical(source_image).dataobj)
    assert np.array_equal(actual, canonical[:, 2, :])


def test_visualize_nifti_supports_axis_count_indices_and_positions(tmp_path):
    path = _save_nifti(
        tmp_path,
        "sampling.nii",
        np.arange(9 * 7 * 5, dtype=np.float32).reshape(9, 7, 5),
    )

    content = visualize.handle(
        {
            "file_path": path,
            "nifti_slice_axis": 0,
            "nifti_num_slices": 4,
            "budget": "small",
        }
    )
    _assert_successful_render(content, expected_images=4)
    report = _text(content)
    assert "source voxel axis 0" in report
    assert "Resolved source indices (0-based): (2, 3, 5, 6)" in report

    content = visualize.handle(
        {
            "file_path": path,
            "nifti_slice_axis": 1,
            "nifti_slice_indices": [6, 0, 6],
            "budget": "small",
        }
    )
    _assert_successful_render(content, expected_images=2)
    report = _text(content)
    assert "Slice selection mode: indices" in report
    assert "Slice count: requested 3; resolved 2" in report
    assert "Resolved source indices (0-based): (6, 0)" in report

    content = visualize.handle(
        {
            "file_path": path,
            "nifti_slice_axis": 2,
            "nifti_slice_positions": [0.0, 0.5, 1.0],
            "budget": "small",
        }
    )
    _assert_successful_render(content)
    report = _text(content)
    assert "Slice selection mode: positions" in report
    assert "Resolved source indices (0-based): (0, 2, 4)" in report
    assert "Requested normalized positions: (0.0, 0.5, 1.0)" in report


def test_visualize_nifti_uses_one_volume_level_percentile_range(tmp_path):
    data = np.zeros((3, 3, 5), dtype=np.float32)
    data[:, :, 0] = 0
    data[:, :, 1] = 25
    data[:, :, 2] = 50
    data[:, :, 3] = 75
    data[:, :, 4] = 100
    path = _save_nifti(tmp_path, "volume-range.nii", data)

    content = visualize.handle(
        {
            "file_path": path,
            "nifti_percentile_low": 0,
            "nifti_percentile_high": 100,
            "budget": "small",
        }
    )

    _assert_successful_render(content)
    report = _text(content)
    assert "Statistics: exact" in report
    assert "Effective display range: [0, 100]" in report
    assert "Configured consistently for: 3 resolved slices" in report

    pixels = np.asarray(nifti._to_grayscale_image(np.array([[0.0, 50.0, 100.0]]), 0.0, 100.0))
    assert sorted(pixels.ravel()) == [0, 128, 255]


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (
            {
                "nifti_intensity_mode": "window",
                "nifti_window_center": 50,
                "nifti_window_width": 400,
            },
            ("Intensity mode: window", "Effective display range: [-150, 250]"),
        ),
        (
            {
                "nifti_intensity_mode": "preset",
                "nifti_window_preset": "ct_lung",
            },
            ("Window preset: ct_lung", "Effective display range: [-1350, 150]"),
        ),
    ],
)
def test_visualize_nifti_manual_and_preset_windows(tmp_path, arguments, expected):
    path = _save_nifti(
        tmp_path,
        "window.nii",
        np.arange(5 * 5 * 5, dtype=np.float32).reshape(5, 5, 5),
    )

    content = visualize.handle(
        {
            "file_path": path,
            "nifti_slice_indices": [2],
            "budget": "small",
            **arguments,
        }
    )

    _assert_successful_render(content, expected_images=1)
    report = _text(content)
    assert all(text in report for text in expected)


def test_visualize_nifti_large_volume_statistics_are_sampled_lazily(tmp_path, monkeypatch):
    shape = (17, 19, 23)
    data = np.arange(math.prod(shape), dtype=np.float32).reshape(shape)
    path = _save_nifti(tmp_path, "sampled.nii.gz", data)
    monkeypatch.setattr(nifti, "MAX_EXACT_VOLUME_BYTES", 0)
    monkeypatch.setattr(nifti, "MAX_INTENSITY_SAMPLES", 100)
    selections = _guard_array_proxy(monkeypatch)

    content = visualize.handle({"file_path": path, "budget": "small"})

    _assert_successful_render(content)
    report = _text(content)
    assert "Statistics: sampled" in report
    assert len(selections) == 4
    sample_selection = selections[0]
    assert all(isinstance(axis, slice) and axis.step > 1 for axis in sample_selection)
    assert all(sum(isinstance(axis, (int, np.integer)) for axis in selection) == 1 for selection in selections[1:])


def test_visualize_nifti_sampled_thin_volume_keeps_a_nonempty_grid(tmp_path, monkeypatch):
    data = np.arange(15, dtype=np.float32).reshape(1, 3, 5)
    path = _save_nifti(tmp_path, "thin.nii.gz", data)
    monkeypatch.setattr(nifti, "MAX_EXACT_VOLUME_BYTES", 0)
    monkeypatch.setattr(nifti, "MAX_INTENSITY_SAMPLES", 1)

    content = visualize.handle({"file_path": path, "budget": "small"})

    _assert_successful_render(content)
    report = _text(content)
    assert "Statistics: sampled" in report
    assert not re.search(r"Finite sample voxels: 0\b", report)
    assert "Fallback: no finite voxels" not in report


def test_visualize_nifti_scaled_integer_estimates_decoded_memory(tmp_path, monkeypatch):
    data = np.arange(4 * 4 * 4, dtype=np.int16).reshape(4, 4, 4)
    image = nib.Nifti1Image(data, np.eye(4))
    image.header.set_slope_inter(2.0, 10.0)
    path = tmp_path / "scaled-int16.nii"
    nib.save(image, path)
    monkeypatch.setattr(nifti, "MAX_EXACT_VOLUME_BYTES", 600)

    content = visualize.handle({"file_path": str(path), "budget": "small"})

    _assert_successful_render(content, expected_images=2)
    report = _text(content)
    assert "Statistics: sampled" in report


@pytest.mark.parametrize("pages", ["999", "0", "3-1", "one", "1,,2"])
def test_visualize_nifti_rejects_invalid_4d_volume_pages(tmp_path, pages):
    path = _save_nifti(
        tmp_path,
        "invalid-pages.nii",
        np.zeros((3, 4, 5, 3), dtype=np.float32),
    )

    content = visualize.handle({"file_path": path, "pages": pages})

    report = _text(content)
    assert report.startswith("Error rendering .nii file:")
    assert "NIfTI volume" in report
    assert not _images(content)


def test_visualize_nifti_caps_total_response_size(tmp_path, monkeypatch):
    rng = np.random.default_rng(7)
    data = rng.normal(size=(64, 64, 9)).astype(np.float32)
    path = _save_nifti(tmp_path, "response-cap.nii", data)
    monkeypatch.setattr(nifti, "MAX_RESPONSE_BYTES", 1_000)

    content = visualize.handle(
        {
            "file_path": path,
            "nifti_num_slices": 7,
            "budget": "small",
        }
    )

    _assert_successful_render(content, expected_images=1)
    report = _text(content)
    assert "Response-size truncation: returned 1 slice across 1 volume" in report
    assert "output truncated at 1 slice across 1 volume" in report
    assert "Returned before response-size truncation: 1 / 7 slices" in report


def test_visualize_nifti_response_cap_omits_unrendered_volume_config(tmp_path, monkeypatch):
    rng = np.random.default_rng(11)
    data = rng.normal(size=(64, 64, 3, 2)).astype(np.float32)
    path = _save_nifti(tmp_path, "response-cap-4d.nii", data)
    monkeypatch.setattr(nifti, "MAX_RESPONSE_BYTES", 1_000)

    content = visualize.handle(
        {
            "file_path": path,
            "pages": "1-2",
            "nifti_num_slices": 1,
            "budget": "small",
        }
    )

    _assert_successful_render(content, expected_images=1)
    report = _text(content)
    assert "**Volume page 1 / index 0**" in report
    assert "**Volume page 2 / index 1**" not in report
    assert report.count("**Effective NIfTI intensity configuration**") == 1
    assert "before volume page 2" in report


def test_visualize_nifti_schema_exposes_position_bounds():
    from mcp_framework import tool_schema

    schema = tool_schema(visualize.VisualizeArgs)
    position_items = schema["properties"]["nifti_slice_positions"]["items"]
    assert position_items["minimum"] == 0.0
    assert position_items["maximum"] == 1.0


def test_visualize_nifti_defaults_unknown_spatial_units_to_mm(tmp_path):
    path = _save_nifti(tmp_path, "unknown-units.nii", np.zeros((3, 4, 5)))

    content = visualize.handle({"file_path": path, "budget": "small"})

    report = _text(content)
    assert "mm (default; NIfTI header unit is unknown)" in report


@pytest.mark.parametrize(
    "arguments,message",
    [
        ({"nifti_slice_axis": 3}, "nifti_slice_axis"),
        ({"nifti_slice_indices": [999]}, "outside source axis range"),
        (
            {
                "nifti_slice_indices": [1],
                "nifti_slice_positions": [0.5],
            },
            "mutually exclusive",
        ),
        (
            {
                "nifti_percentile_low": 99,
                "nifti_percentile_high": 1,
            },
            "0 <= low < high <= 100",
        ),
        (
            {"nifti_intensity_mode": "window", "nifti_window_center": 40},
            "requires both",
        ),
        (
            {"nifti_intensity_mode": "preset"},
            "requires nifti_window_preset",
        ),
    ],
)
def test_visualize_nifti_rejects_invalid_options(tmp_path, arguments, message):
    path = _save_nifti(tmp_path, "invalid-options.nii", np.zeros((3, 4, 5)))

    content = visualize.handle({"file_path": path, **arguments})

    report = _text(content)
    assert report.startswith("Error rendering .nii file:")
    assert message in report
    assert not _images(content)


def test_visualize_nifti_corrupt_compound_extension_returns_renderer_error(tmp_path):
    path = tmp_path / "corrupt.nii.gz"
    path.write_bytes(b"not a nifti file")

    content = visualize.handle({"file_path": str(path)})

    report = _text(content)
    assert any(line.startswith("Error") for line in report.splitlines()), report
    # In particular, .nii.gz must be recognized as a compound extension and reach
    # the renderer instead of being rejected as an unsupported generic .gz file.
    assert "unsupported file type" not in report.lower()


@pytest.mark.parametrize(
    "case,data",
    [
        ("constant", np.full((7, 9, 11), 5, dtype=np.float32)),
        (
            "nonfinite",
            np.pad(
                np.array([[[np.nan, np.inf, -np.inf, 1.0, 2.0]]], dtype=np.float32),
                ((3, 3), (4, 4), (3, 3)),
                constant_values=0,
            ),
        ),
    ],
)
def test_visualize_nifti_handles_degenerate_intensities(tmp_path, case, data):
    path = _save_nifti(tmp_path, f"{case}.nii", data)

    content = visualize.handle({"file_path": path, "budget": "small"})

    _assert_successful_render(content)


def test_visualize_nifti_rejects_dimensions_other_than_3d_or_4d(tmp_path):
    shape = (3, 4, 5, 2, 2)
    path = _save_nifti(tmp_path, "unsupported-dimensions.nii", np.zeros(shape, dtype=np.float32))

    content = visualize.handle({"file_path": path})

    report = _text(content)
    assert report.startswith("Error rendering .nii file:")
    assert "supports 3D or 4D" in report
    assert not _images(content)
