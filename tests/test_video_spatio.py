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
    """Two high-resolution frames for geometry and rendering tests."""
    from PIL import Image

    paths = []
    for i in range(2):
        p = tmp_path / f"frame_{i}.png"
        Image.new("RGB", (FRAME_W, FRAME_H), (20 * i, 40, 60)).save(p)
        paths.append(str(p))
    return paths


def _build_scene(frames, objects_by_frame, camera_motions=None, **kwargs):
    from qwen_mm_plugins_video_spatio.tools import build_scene

    args = {"objects_by_frame": objects_by_frame, "frames": frames, **kwargs}
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
    scene = _build_scene(frames[:1], [[{"label": "chair", "bbox": bbox, "depth_m": 2.5}]], bbox_format="pixels")

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
    scene = _build_scene(
        frames[:1], [[{"label": "cup", "bbox": [0.1, 0.2, 0.3, 0.4], "depth_m": 1.0}]], bbox_format="normalized"
    )
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


def test_normalized_boxes_do_not_depend_on_image_resolution(frames):
    bbox = [100, 200, 300, 400]
    scene = _build_scene(frames[:1], [[{"label": "chair", "bbox": bbox, "depth_m": 2}]])
    assert scene["instances"]["0"][0]["bbox_1000"] == bbox
    assert scene["instances"]["0"][0]["bbox_pixel"] == [160, 240, 480, 480]


@pytest.mark.parametrize(
    "extra",
    [
        {"frame_indices": [0]},
        {"frame_indices": [0, 0]},
        {"camera_motions": []},
        {"camera_motions": [{"yaw_deg": float("nan")}]},
    ],
)
def test_build_scene_rejects_invalid_frame_metadata(frames, extra):
    from qwen_mm_plugins_video_spatio.tools import build_scene

    blocks = build_scene.handle({"frames": frames, "objects_by_frame": [[], []], **extra})
    assert blocks[0]["text"].startswith("Error"), blocks


@pytest.mark.parametrize(
    "obj",
    [
        {"label": "chair", "bbox": [1, 2, 3], "depth_m": 2},
        {"label": "chair", "bbox": [100, 200, 300, 400], "depth_m": -1},
        {"label": "chair", "bbox": [100, 200, 300, 400], "depth_m": float("inf")},
        {"label": "chair", "bbox": [300, 200, 100, 400], "depth_m": 2},
    ],
)
def test_build_scene_rejects_invalid_objects(frames, obj):
    from qwen_mm_plugins_video_spatio.tools import build_scene

    blocks = build_scene.handle({"frames": frames[:1], "objects_by_frame": [[obj]]})
    assert blocks[0]["text"].startswith("Error"), blocks


def test_camera_translation_uses_the_previous_heading(frames):
    scene = _build_scene(
        [*frames, frames[0]],
        [[], [], []],
        camera_motions=[
            {"yaw_deg": 90},
            {"forward_m": 2},
        ],
    )
    assert scene["cameras"]["2"]["pos_bev"] == pytest.approx([2, 0], abs=1e-8)


def test_object_world_motion_reports_forward_along_negative_z(frames):
    from qwen_mm_plugins_video_spatio.tools import object_world_motion

    scene = _build_scene(
        frames, [[{"label": "chair", "bbox": [400, 400, 600, 600], "depth_m": depth}] for depth in [2, 3]]
    )
    result = json.loads(object_world_motion.handle({"scene": scene, "target": "chair"})[0]["text"])
    assert result["direction"] == "forward"
    assert result["move_vec"] == [0, -1]


@pytest.mark.parametrize(
    "positions,bearings,reliable",
    [
        ([[0, 0], [2, 0]], [0, -20], True),
        ([[0, 0], [0, 0]], [0, 20], False),
        ([[0, 0], [0, -4]], [0, 180], False),
        ([[0, 0], [2, 0]], [0, 20], False),
    ],
)
def test_triangulation_rejects_degenerate_or_backward_rays(positions, bearings, reliable):
    from qwen_mm_plugins_video_spatio.tools import triangulate

    scene = {
        "type": "video-spatio/scene@1",
        "frame_indices": [0, 1],
        "instances": {str(i): [{"label": "chair", "point_1000": [500, 500], "depth_m": 3}] for i in range(2)},
        "cameras": {str(i): {"pos_bev": positions[i], "yaw_deg": bearings[i]} for i in range(2)},
    }
    result = json.loads(triangulate.handle({"scene": scene, "target": "chair", "frame_a": 0, "frame_b": 1})[0]["text"])
    assert result["reliable"] is reliable
    if reliable:
        assert result["world_xz"] == pytest.approx([0, -2 / math.tan(math.radians(20))], abs=1e-4)


def test_claude_model_uses_the_configured_openai_compatible_transport(monkeypatch):
    from types import SimpleNamespace

    from PIL import Image

    from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim
    from shared import api_openai

    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="chair"))])

    monkeypatch.setattr(api_openai, "call_openai_chat", chat)
    shim = VLMShim(model="anthropic/claude-test", base_url="http://localhost:1234/v1", api_key="test-only")
    assert shim.ask(Image.new("RGB", (10, 10)), "What is visible?") == "chair"
    assert calls[0]["base_url"] == "http://localhost:1234/v1"
    assert calls[0]["model"] == "anthropic/claude-test"
    assert calls[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize(
    "tool,args",
    [
        ("assess_coverage", {}),
        ("assess_reachable", {"target": "chair"}),
        ("plan_exploration", {"target": "door"}),
        ("calibrate_scale", {"target": "chair", "known_size_m": 1}),
        ("count_objects", {}),
        ("count_objects", {"frame": 0}),
        ("match_entities", {}),
        ("match_entities", {"op": "deduplicate"}),
        ("mobile_manip", {"target": "chair"}),
        ("mobile_manip", {"op": "reachability", "target": "chair"}),
        ("mobile_manip", {"op": "plan_active_search", "target": "door"}),
        ("render_scene_views", {}),
        ("scene_map", {}),
        ("scene_map", {"op": "appearance_order"}),
        ("scene_map", {"op": "diff_frames", "frame_a": 0, "frame_b": 1}),
        ("select_keyframes", {}),
        ("select_keyframes", {"strategy": "uniform", "total_frames": 20, "n": 3}),
        ("select_keyframes", {"strategy": "coverage", "label": "chair"}),
        ("view_reason", {"frame": 0, "viewpoint": "chair"}),
        ("motion", {"past_points": [[0, 0], [1, 1], [2, 2]], "order": 1}),
    ],
)
def test_tools_return_mcp_content_with_offline_scene(frames, monkeypatch, tool, args):
    import importlib

    from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

    # No remote calls: exercise real geometry/rendering with a deterministic VLM fallback.
    monkeypatch.setattr(VLMShim, "_dispatch", lambda *a, **kw: "Unknown")
    scene = _build_scene(frames, [[{"label": "chair", "bbox": [400, 400, 600, 600], "depth_m": 2}]] * 2)
    module = importlib.import_module(
        "qwen_mm_plugins_video_spatio.tools." + ("explore" if tool == "assess_coverage" else tool)
    )
    blocks = module.handle({"scene": scene, **args})
    assert blocks
    for block in blocks:
        assert block["type"] in {"text", "image"}
        if block["type"] == "text":
            assert not block["text"].startswith("Error"), blocks


@pytest.mark.parametrize("size", [(640, 480), (1600, 1200)])
def test_grounding_crop_uses_normalized_boxes_at_every_resolution(size):
    from PIL import Image

    from qwen_mm_plugins_video_spatio.experts.grounding_verifier import GroundingVerifier

    cropped = GroundingVerifier._crop_bbox(Image.new("RGB", size), [250, 250, 750, 750])
    assert cropped.size == (int(size[0] * 0.6), int(size[1] * 0.6))
