"""Tests for the video-spatio capability: model-free spatial reasoning.

The capability is prompt-only — the HOST model does the perception and passes grounded boxes +
depth estimates in, so every geometry tool is pure compute and testable offline. These cover the
MCP protocol surface (all tools discoverable over stdio), `build_scene`'s bbox/camera-pose math
(the contract every downstream tool consumes), a BEV render, and that a VLM-backed tool degrades
to an error block instead of crashing when no endpoint answers.
"""

import json
import math
import os

import pytest
from conftest import mcp_call

pytest.importorskip("numpy")

_SPATIO_SERVER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src",
    "capabilities",
    "video-spatio",
    "qwen_mm_plugins_video_spatio",
)

# The full flattened tool surface: geometry/planning tools plus the few single-shot VLM ones.
EXPECTED_TOOLS = {
    "assess_coverage",
    "assess_reachable",
    "build_scene",
    "calibrate_scale",
    "camera_motion",
    "count_objects",
    "match_entities",
    "mobile_manip",
    "motion",
    "object_world_motion",
    "orient_facing",
    "plan_exploration",
    "render_scene_views",
    "scene_map",
    "select_keyframes",
    "triangulate",
    "verify_grounding",
    "view_reason",
    "visualize_bev",
}


FRAME_W, FRAME_H = 1600, 1200


@pytest.fixture
def frames(tmp_path):
    """Two frames — only their size matters for the geometry (intrinsics are derived from it).

    Both dimensions are >= 1000 so `build_scene` reads a bbox as raw pixels; on a smaller frame a
    0-1000-ranged bbox is ambiguous and taken as already normalized.
    """
    from PIL import Image

    paths = []
    for i in range(2):
        p = tmp_path / f"frame_{i}.png"
        Image.new("RGB", (FRAME_W, FRAME_H), (20 * i, 40, 60)).save(p)
        paths.append(str(p))
    return paths


def _build_scene(frames, objects_by_frame, camera_motions=None):
    from qwen_mm_plugins_video_spatio.tools import build_scene

    args = {"objects_by_frame": objects_by_frame, "frames": frames}
    if camera_motions is not None:
        args["camera_motions"] = camera_motions
    blocks = build_scene.handle(args)
    assert not blocks[0]["text"].startswith("Error"), blocks[0]["text"]
    return json.loads(blocks[-1]["text"])


def test_server_lists_every_tool_over_stdio():
    result = mcp_call(_SPATIO_SERVER_DIR, lambda s: s.list_tools())
    assert {t.name for t in result.tools} == EXPECTED_TOOLS


def test_tools_advertise_descriptions_and_schemas():
    result = mcp_call(_SPATIO_SERVER_DIR, lambda s: s.list_tools())
    for tool in result.tools:
        assert tool.description, tool.name
        assert tool.inputSchema.get("type") == "object", tool.name


def test_build_scene_converts_pixel_bboxes_to_the_0_1000_convention(frames):
    # The bottom-right quadrant in pixels -> x in [500,1000], y in [500,1000] normalized.
    bbox = [FRAME_W / 2, FRAME_H / 2, FRAME_W, FRAME_H]
    scene = _build_scene(frames[:1], [[{"label": "chair", "bbox": bbox, "depth_m": 2.5}]])

    assert scene["type"] == "video-spatio/scene@1"
    assert scene["image_size"] == {"w": FRAME_W, "h": FRAME_H}
    inst = scene["instances"]["0"][0]
    assert inst["label"] == "chair"
    assert inst["bbox_1000"] == pytest.approx([500.0, 500.0, 1000.0, 1000.0])
    assert inst["point_1000"] == pytest.approx([750.0, 750.0])
    assert inst["depth_m"] == pytest.approx(2.5)
    # FOV=60 deg -> fx = 0.5*W/tan(30 deg), cx/cy at the image centre.
    assert scene["intrinsics"]["fx"] == pytest.approx(0.5 * FRAME_W / math.tan(math.radians(30)), rel=1e-6)
    assert (scene["intrinsics"]["cx"], scene["intrinsics"]["cy"]) == (FRAME_W / 2, FRAME_H / 2)


def test_build_scene_accepts_0_1_normalized_bboxes(frames):
    scene = _build_scene(frames[:1], [[{"label": "cup", "bbox": [0.1, 0.2, 0.3, 0.4], "depth_m": 1.0}]])
    assert scene["instances"]["0"][0]["bbox_1000"] == pytest.approx([100.0, 200.0, 300.0, 400.0])


def test_build_scene_rejects_misaligned_object_lists(frames):
    from qwen_mm_plugins_video_spatio.tools import build_scene

    blocks = build_scene.handle({"objects_by_frame": [[]], "frames": frames})
    assert "must align 1:1" in blocks[0]["text"]


def test_build_scene_without_motions_leaves_cameras_at_the_origin(frames):
    scene = _build_scene(frames, [[], []])
    for cam in scene["cameras"].values():
        assert cam["pos_bev"] == pytest.approx([0.0, 0.0])
        assert cam["yaw_deg"] == pytest.approx(0.0)


def test_camera_motions_accumulate_into_poses(frames):
    """+forward_m walks along -Z, +yaw_deg turns right — the convention the SKILL documents."""
    scene = _build_scene(frames, [[], []], camera_motions=[{"yaw_deg": 90.0, "forward_m": 2.0, "right_m": 0.0}])

    cam0, cam1 = scene["cameras"]["0"], scene["cameras"]["1"]
    assert cam0["pos_bev"] == pytest.approx([0.0, 0.0])
    assert cam1["pos_bev"] == pytest.approx([0.0, -2.0], abs=1e-6)
    assert cam1["yaw_deg"] == pytest.approx(90.0, abs=1e-6)
    assert cam1["yaw_delta_deg"] == pytest.approx(90.0, abs=1e-6)


def test_camera_motion_reports_the_relative_turn(frames):
    from qwen_mm_plugins_video_spatio.tools import camera_motion

    scene = _build_scene(frames, [[], []], camera_motions=[{"yaw_deg": 90.0, "forward_m": 2.0, "right_m": 0.0}])
    blocks = camera_motion.handle({"scene": scene, "frame_i": 0, "frame_j": 1})
    text = "\n".join(b.get("text", "") for b in blocks)
    assert "90" in text


def test_visualize_bev_returns_an_image(frames):
    pytest.importorskip("matplotlib")
    from qwen_mm_plugins_video_spatio.tools import visualize_bev

    scene = _build_scene(
        frames,
        [
            [{"label": "chair", "bbox": [100, 200, 300, 400], "depth_m": 2.0}],
            [{"label": "chair", "bbox": [120, 200, 320, 400], "depth_m": 2.2}],
        ],
        camera_motions=[{"yaw_deg": 10.0, "forward_m": 0.5, "right_m": 0.0}],
    )
    blocks = visualize_bev.handle({"scene": scene, "title": "test BEV"})
    assert any(b.get("type") == "image" and b.get("data") for b in blocks), blocks


def test_vlm_tool_degrades_to_an_error_block_without_a_reachable_endpoint(frames, monkeypatch):
    """A single-shot VLM tool must return an error block, never raise out of the handler."""
    from qwen_mm_plugins_video_spatio.tools import orient_facing

    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "not-a-key")
    monkeypatch.setenv("QWEN_MM_API_VL_MODEL", "does-not-exist")

    blocks = orient_facing.handle({"image": frames[0], "target": "chair", "votes": 1})
    assert blocks and all(b.get("type") == "text" for b in blocks)
    assert "error" in "\n".join(b.get("text", "") for b in blocks).lower()
