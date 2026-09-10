"""Offline coverage for the standalone NIfTI visualization plugin.

Tests use generated synthetic fixtures plus a public MNI152 population-average template; no
individual patient scan is included.
"""

import asyncio
import base64
import io
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import qwen_mm_plugins_nifti as plugin
from conftest import mcp_call
from PIL import Image
from pydantic import ValidationError
from qwen_mm_plugins_nifti.renderers import nifti
from qwen_mm_plugins_nifti.tools import visualize

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
    if not path.is_file():
        pytest.skip("public MNI152 template is not present")

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


def test_visualize_nifti_4d_defaults_and_selects_volumes_as_3d_volumes(tmp_path, monkeypatch):
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
    assert "Selected 3D volume: number 1 / index 0 (of 3)" in report
    assert "defaulted to the first 3D volume" in report
    assert "**Volume number 1 / index 0**" in report
    low, high = np.percentile(first, (1, 99))
    assert f"Effective display range: [{low:.6g}, {high:.6g}]" in report
    assert len(selections) == 1
    assert selections[0][-1] == 0
    assert all(isinstance(axis, slice) for axis in selections[0][:3])

    selections.clear()
    content = visualize.handle(
        {
            "file_path": path,
            "volumes": "1-3",
            "max_volumes": 2,
            "budget": "small",
        }
    )

    report = _text(content)
    assert len(_images(content)) == 6
    assert "Selected 3D volumes: numbers (1, 2); indices (0, 1) (of 3)" in report
    assert "**Volume number 1 / index 0**" in report
    assert "**Volume number 2 / index 1**" in report
    assert "Volume number 3" not in report
    assert "Volume selection was truncated by max_volumes" in report
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
            "slice_axis": 0,
            "num_slices": 4,
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
            "slice_axis": 1,
            "slice_indices": [6, 0, 6],
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
            "slice_axis": 2,
            "slice_positions": [0.0, 0.5, 1.0],
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
            "percentile_low": 0,
            "percentile_high": 100,
            "budget": "small",
        }
    )

    decoded = _assert_successful_render(content)
    report = _text(content)
    assert "Statistics: exact" in report
    assert "Effective display range: [0, 100]" in report
    assert "Configured consistently for: 3 resolved slices" in report

    # Constant slices must retain different brightness under a shared volume range.
    # Slice-wise percentiles would collapse every one of these images to black.
    for image, expected in zip(decoded, (64, 128, 191), strict=True):
        assert float(np.asarray(image).mean()) == pytest.approx(expected, abs=2)


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (
            {
                "intensity_mode": "window",
                "window_center": 50,
                "window_width": 400,
            },
            ("Intensity mode: window", "Effective display range: [-150, 250]"),
        ),
        (
            {
                "intensity_mode": "preset",
                "window_preset": "ct_lung",
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
            "slice_indices": [2],
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


@pytest.mark.parametrize("volumes", ["999", "0", "3-1", "one", "1,,2"])
def test_visualize_nifti_rejects_invalid_4d_volume_selection(tmp_path, volumes):
    path = _save_nifti(
        tmp_path,
        "invalid-volumes.nii",
        np.zeros((3, 4, 5, 3), dtype=np.float32),
    )

    content = visualize.handle({"file_path": path, "volumes": volumes})

    report = _text(content)
    assert report.startswith("Error")
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
            "num_slices": 7,
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
            "volumes": "1-2",
            "num_slices": 1,
            "budget": "small",
        }
    )

    _assert_successful_render(content, expected_images=1)
    report = _text(content)
    assert "**Volume number 1 / index 0**" in report
    assert "**Volume number 2 / index 1**" not in report
    assert report.count("**Effective NIfTI intensity configuration**") == 1
    assert "before volume number 2" in report


def test_nifti_registry_advertises_the_standalone_tool_and_documented_schema():
    tools = plugin.list_tools()
    assert [tool["name"] for tool in tools] == ["nifti_visualize"]
    assert plugin.get_handler("nifti_visualize") is visualize.handle
    assert plugin.get_handler("visualize") is None
    tool = tools[0]
    schema = tool["inputSchema"]
    assert schema["required"] == ["file_path"]
    assert tool["description"]
    fields = schema["properties"]
    assert all(field.get("description") for field in fields.values())
    assert fields["slice_axis"]["default"] == 2
    assert fields["num_slices"]["default"] == 3
    assert fields["num_slices"]["maximum"] == 20
    assert fields["intensity_mode"]["default"] == "auto_volume"
    assert fields["percentile_low"]["default"] == 1
    assert fields["percentile_high"]["default"] == 99
    assert "volumes" in fields and "max_volumes" in fields
    assert "pages" not in fields and "max_pages" not in fields
    assert not any(field.startswith("nifti_") for field in fields)
    position_items = fields["slice_positions"]["items"]
    assert position_items["minimum"] == 0.0
    assert position_items["maximum"] == 1.0
    assert "$ref" not in json.dumps(schema)


def test_visualize_nifti_defaults_unknown_spatial_units_to_mm(tmp_path):
    path = _save_nifti(tmp_path, "unknown-units.nii", np.zeros((3, 4, 5)))

    content = visualize.handle({"file_path": path, "budget": "small"})

    report = _text(content)
    assert "mm (default; NIfTI header unit is unknown)" in report


@pytest.mark.parametrize(
    "arguments,message",
    [
        ({"slice_axis": 3}, "slice_axis"),
        ({"slice_indices": [999]}, "outside source axis range"),
        (
            {
                "slice_indices": [1],
                "slice_positions": [0.5],
            },
            "mutually exclusive",
        ),
        (
            {
                "percentile_low": 99,
                "percentile_high": 1,
            },
            "0 <= low < high <= 100",
        ),
        (
            {"intensity_mode": "window", "window_center": 40},
            "requires both",
        ),
        (
            {"intensity_mode": "preset"},
            "requires window_preset",
        ),
    ],
)
def test_visualize_nifti_rejects_invalid_options(tmp_path, arguments, message):
    path = _save_nifti(tmp_path, "invalid-options.nii", np.zeros((3, 4, 5)))

    content = visualize.handle({"file_path": path, **arguments})

    report = _text(content)
    assert report.startswith("Error")
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
    assert report.startswith("Error")
    assert "supports 3D or 4D" in report
    assert not _images(content)


@pytest.mark.parametrize(
    "arguments",
    [
        {"slice_axis": True},
        {"num_slices": 2.5},
        {"num_slices": 21},
        {"slice_indices": [1.5]},
        {"slice_positions": [float("nan")]},
        {"slice_positions": [1.1]},
        {"window_center": float("inf")},
        {"window_width": -1},
        {"max_volumes": 0},
        {"pages": "1"},
    ],
)
def test_nifti_rejects_invalid_values_in_direct_calls_and_discovered_model(tmp_path, arguments):
    path = _save_nifti(tmp_path, "validation.nii", np.zeros((3, 4, 5)))
    arguments = {"file_path": path, **arguments}
    spec = next(spec for spec in plugin.SPECS if spec.name == "nifti_visualize")
    with pytest.raises(ValidationError):
        spec.args_model.model_validate(arguments)

    content = plugin.get_handler("nifti_visualize")(arguments)
    assert _text(content).startswith("Error")
    assert not _images(content)


@pytest.mark.parametrize(
    "arguments,invalid_field",
    [
        ({"slice_axis": True}, "slice_axis"),
        ({"num_slices": 2.0}, "num_slices"),
        ({"max_volumes": "2"}, "max_volumes"),
        ({"slice_indices": [True]}, "slice_indices"),
        ({"slice_positions": [False]}, "slice_positions"),
        ({"slice_positions": [float("nan")]}, "slice_positions"),
        ({"percentile_low": "1"}, "percentile_low"),
        (
            {"intensity_mode": "window", "window_center": float("nan"), "window_width": 1.0},
            "window_center",
        ),
        (
            {"intensity_mode": "window", "window_center": 0.0, "window_width": float("inf")},
            "window_width",
        ),
    ],
)
def test_nifti_fastmcp_rejects_invalid_declared_numeric_fields(arguments, invalid_field):
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    from mcp.server.fastmcp.exceptions import ToolError

    from mcp_framework import _make_wrapper

    spec = next(spec for spec in plugin.SPECS if spec.name == "nifti_visualize")
    server = fastmcp.FastMCP("nifti-validation-test")
    server.add_tool(_make_wrapper(spec), name=spec.name, description=spec.description, structured_output=False)
    # Exercise FastMCP's initial argument model as well as the framework wrapper.
    # Invalid fields must fail before handler file lookup, without coercing booleans
    # or strings into different numeric settings.
    with pytest.raises(ToolError, match=invalid_field):
        asyncio.run(server.call_tool("nifti_visualize", {"file_path": "/missing.nii", **arguments}))


def test_nifti_3d_volume_selection_accepts_only_volume_one(tmp_path):
    path = _save_nifti(tmp_path, "three-dimensional.nii", np.zeros((3, 4, 5)))
    content = visualize.handle({"file_path": path, "volumes": "1", "budget": "small"})
    _assert_successful_render(content)

    for volumes in ("2", "1-2", "", " "):
        content = visualize.handle({"file_path": path, "volumes": volumes})
        assert _text(content).startswith("Error"), volumes
        assert not _images(content), volumes


def test_nifti_reports_missing_local_input_and_unsupported_extension(tmp_path):
    missing = visualize.handle({"file_path": str(tmp_path / "missing.nii.gz")})
    assert "file not found" in _text(missing).lower()
    assert not _images(missing)

    text_path = tmp_path / "not-nifti.txt"
    text_path.write_text("not a nifti file")
    unsupported = visualize.handle({"file_path": str(text_path)})
    assert _text(unsupported).startswith("Error")
    assert ".nii" in _text(unsupported)
    assert not _images(unsupported)

    remote = visualize.handle({"file_path": "https://example.invalid/volume.nii.gz"})
    assert _text(remote).startswith("Error")
    assert "local" in _text(remote).lower()
    assert not _images(remote)


def test_nifti_starts_lazily_and_renders_without_sibling_capabilities(tmp_path):
    path = _save_nifti(tmp_path, "standalone.nii", np.arange(60, dtype=np.float32).reshape(3, 4, 5))
    repo = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc
import sys

sys.path[:0] = [sys.argv[1], sys.argv[2]]

class BlockImports(importlib.abc.MetaPathFinder):
    deny_heavy = True

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split('.')[0]
        if root.startswith('qwen_mm_plugins_') and root != 'qwen_mm_plugins_nifti':
            raise ImportError('Standalone NIfTI must not import sibling ' + fullname)
        if self.deny_heavy and root in {'numpy', 'nibabel', 'PIL'}:
            raise ImportError('Heavy dependency imported during discovery: ' + fullname)

blocker = BlockImports()
sys.meta_path.insert(0, blocker)
import qwen_mm_plugins_nifti as plugin
assert [tool['name'] for tool in plugin.list_tools()] == ['nifti_visualize']
assert not {'numpy', 'nibabel', 'PIL'}.intersection(sys.modules)
blocker.deny_heavy = False
content = plugin.get_handler('nifti_visualize')({'file_path': sys.argv[3], 'budget': 'small'})
assert len([block for block in content if block['type'] == 'image']) == 3, content
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(repo / "src"), str(repo / "src/capabilities/nifti"), path],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_nifti_stdio_discovers_and_renders_with_its_own_server(tmp_path):
    pytest.importorskip("mcp")
    path = _save_nifti(tmp_path, "protocol.nii.gz", np.arange(60, dtype=np.float32).reshape(3, 4, 5))

    async def action(session):
        tools = await session.list_tools()
        assert [tool.name for tool in tools.tools] == ["nifti_visualize"]
        assert tools.tools[0].inputSchema["properties"]["slice_axis"]["default"] == 2
        return await session.call_tool(
            "nifti_visualize",
            {"file_path": path, "slice_indices": [1], "budget": "small"},
        )

    content = mcp_call(
        str(Path(plugin.__file__).parent),
        action,
        env={**os.environ, "QWEN_MM_NATIVE_MODE": "true"},
    )
    assert not content.isError
    blocks = [block.model_dump() for block in content.content]
    _assert_successful_render(blocks, expected_images=1)
    assert "Resolved source indices (0-based): (1,)" in _text(blocks)
