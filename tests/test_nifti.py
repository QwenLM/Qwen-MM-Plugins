"""Synthetic, offline coverage for NIfTI visualization.

The fixtures are generated at test time and contain no patient or clinical data.
"""

import base64
import builtins
import io
import re

import numpy as np
import pytest
from PIL import Image

from qwen_mm_plugins_core.visualizers import visualize

nib = pytest.importorskip("nibabel")


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


def _is_lazy_plane_selection(selection: tuple, ndim: int) -> bool:
    """Return whether a proxy selection reads one spatial plane, not a volume."""
    if len(selection) != ndim:
        return False
    spatial = selection[:3]

    def fixes_one_coordinate(axis) -> bool:
        if isinstance(axis, (int, np.integer)):
            return True
        return (
            isinstance(axis, slice)
            and isinstance(axis.start, (int, np.integer))
            and isinstance(axis.stop, (int, np.integer))
            and axis.stop - axis.start == 1
        )

    return sum(fixes_one_coordinate(axis) for axis in spatial) == 1


def _assert_successful_three_plane_render(content: list[dict]) -> list[Image.Image]:
    report = _text(content)
    assert not any(line.startswith("Error") for line in report.splitlines()), report

    blocks = _images(content)
    assert len(blocks) == 3, f"expected axial/coronal/sagittal images, got {len(blocks)}: {report}"

    decoded = []
    for block in blocks:
        assert block.get("mimeType", "").startswith("image/")
        image = Image.open(io.BytesIO(base64.b64decode(block["data"])))
        image.load()
        assert image.width > 0 and image.height > 0
        decoded.append(image)
    return decoded


@pytest.mark.parametrize("suffix", [".nii", ".nii.gz"])
def test_visualize_nifti_reports_metadata_and_three_planes(tmp_path, suffix):
    shape = (7, 9, 11)
    x, y, z = np.indices(shape, dtype=np.float32)
    data = x + 10 * y + 100 * z
    path = _save_nifti(tmp_path, f"synthetic{suffix}", data)

    content = visualize.handle({"file_path": path, "budget": "small"})

    _assert_successful_three_plane_render(content)
    report = _text(content)
    report_lower = report.lower()
    assert "nifti" in report_lower
    assert "shape" in report_lower and re.search(r"7\D+9\D+11", report)
    assert "dtype" in report_lower and "float32" in report_lower
    assert ("voxel spacing" in report_lower or "zooms" in report_lower) and all(
        value in report for value in ("2.0", "3.0", "4.0")
    )
    assert "orientation" in report_lower and "RAS" in report.upper()
    assert "affine" in report_lower
    assert all(plane in report_lower for plane in ("axial", "coronal", "sagittal"))


def test_visualize_nifti_4d_defaults_to_volume_zero_lazily(tmp_path, monkeypatch):
    shape = (7, 9, 11)
    x, y, z = np.indices(shape, dtype=np.float32)
    first = x + 10 * y + 100 * z
    second = np.full(shape, 42, dtype=np.float32)
    data = np.stack([first, second], axis=-1)
    path = _save_nifti(tmp_path, "synthetic-4d.nii.gz", data)
    selections = _guard_array_proxy(monkeypatch)

    content = visualize.handle({"file_path": path, "budget": "small"})

    decoded = _assert_successful_three_plane_render(content)
    report = _text(content)
    assert re.search(r"volume(?:\s+index)?\s*[:=]?\s*0\b", report, re.IGNORECASE), report
    assert re.search(r"\bof\s+2\b", report, re.IGNORECASE), report
    assert len(selections) == 3
    for selection in selections:
        assert _is_lazy_plane_selection(selection, ndim=4) and selection[-1] == 0
    # Volume 1 is constant. Each center slice varying in intensity proves the default
    # render came from the first volume, rather than an arbitrary time point.
    for image in decoded:
        low, high = image.convert("L").getextrema()
        assert low < high, "4D visualization did not render the non-constant volume 0"


@pytest.mark.parametrize("four_dimensional", [False, True], ids=["3d", "4d"])
def test_visualize_nifti_noncanonical_orientation_matches_ras_lazily(tmp_path, monkeypatch, four_dimensional):
    # Even dimensions make a flipped center index differ by one in source space,
    # catching the common ``size // 2`` off-by-one error after reorientation.
    shape = (6, 8, 10)
    x, y, z = np.indices(shape, dtype=np.float32)
    first = x + 10 * y + 100 * z
    data = np.stack([first, np.full(shape, 42, dtype=np.float32)], axis=-1) if four_dimensional else first
    affine = np.diag([2.0, 3.0, 4.0, 1.0])
    reference_path = _save_nifti(tmp_path, "ras-reference.nii", data, affine)
    reference = visualize.handle({"file_path": reference_path, "budget": "small"})
    reference_images = _assert_successful_three_plane_render(reference)

    ras_image = nib.Nifti1Image(data, affine)
    transform = nib.orientations.ornt_transform(
        nib.orientations.axcodes2ornt(("R", "A", "S")),
        nib.orientations.axcodes2ornt(("S", "L", "A")),
    )
    source_image = ras_image.as_reoriented(transform)
    path = tmp_path / "sla-source.nii.gz"
    nib.save(source_image, path)
    selections = _guard_array_proxy(monkeypatch)

    content = visualize.handle({"file_path": str(path), "budget": "small"})

    rendered_images = _assert_successful_three_plane_render(content)
    report = _text(content)
    assert re.search(r"orientation.*RAS.*source\s+SLA", report, re.IGNORECASE), report
    assert len(selections) == 3
    ndim = 4 if four_dimensional else 3
    assert all(_is_lazy_plane_selection(selection, ndim) for selection in selections)
    if four_dimensional:
        assert all(selection[-1] == 0 for selection in selections)
    for expected, actual in zip(reference_images, rendered_images, strict=True):
        assert np.array_equal(np.asarray(expected), np.asarray(actual))


@pytest.mark.parametrize("suffix", [".nii", ".nii.gz"])
def test_visualize_nifti_corrupt_file_returns_renderer_error(tmp_path, suffix):
    path = tmp_path / f"corrupt{suffix}"
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

    _assert_successful_three_plane_render(content)


@pytest.mark.parametrize("shape", [(7, 9), (3, 4, 5, 2, 2)], ids=["2d", "5d"])
def test_visualize_nifti_rejects_dimensions_other_than_3d_or_4d(tmp_path, shape):
    path = _save_nifti(tmp_path, "unsupported-dimensions.nii", np.zeros(shape, dtype=np.float32))

    content = visualize.handle({"file_path": path})

    report = _text(content)
    assert report.startswith("Error rendering .nii file:")
    assert "supports 3D or 4D" in report
    assert not _images(content)


def test_visualize_nifti_missing_dependency_is_actionable(tmp_path, monkeypatch):
    path = _save_nifti(tmp_path, "missing-dependency.nii", np.zeros((3, 4, 5), dtype=np.float32))
    original_import = builtins.__import__

    def reject_nibabel(name, *args, **kwargs):
        if name == "nibabel" or name.startswith("nibabel."):
            raise ImportError("simulated missing nibabel")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_nibabel)

    content = visualize.handle({"file_path": path})

    report = _text(content)
    assert report.startswith("Error rendering .nii file:")
    assert "qwen-mm-plugins[viz]" in report
