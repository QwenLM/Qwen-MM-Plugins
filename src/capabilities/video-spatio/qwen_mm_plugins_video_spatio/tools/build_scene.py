"""MCP tool: build_scene — assemble an instance-level scene from OUTER-model perception.

Model-free / prompt-only deployment: the OUTER model (Claude Code) does the perception
(grounds salient/target objects + estimates each one's metric depth) and passes the result
in as ``objects_by_frame``. This tool is PURE COMPUTE — no VLM call: it normalizes bboxes to
the 0..1000 convention, assembles per-frame instances + camera poses (pinhole, FOV=60°, world
+Y up / forward -Z), and returns a serializable ``scene`` JSON that every downstream geometry
tool (visualize_bev / triangulate / object_world_motion / calibrate_scale / render_scene_views)
takes back as its ``scene`` argument.

Object format the outer model must produce per frame (mirrors the old _ESTIMATE_PROMPT):
  {"label": "chair", "bbox": [x1,y1,x2,y2], "depth_m": 2.3}
  bbox = [x1,y1,x2,y2], x FIRST then y, (x1,y1)=top-left; accepted as 0..1 normalized,
  0..1000 normalized, or raw pixels (auto-detected). depth_m = positive float (meters).
"""

from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.content import json_text, require_dep, text, text_error

_SCENE_FOV_DEG = 60.0
_OBJ_DEPTH_CONF = 1.0
SCENE_SCHEMA = "video-spatio/scene@1"


class SceneObject(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    label: str = Field(min_length=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    depth_m: float = Field(gt=0)


class CameraMotion(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    yaw_deg: float = 0.0
    forward_m: float = 0.0
    right_m: float = 0.0
    pitch_deg: float = 0.0


class BuildSceneArgs(BaseModel):
    objects_by_frame: list[list[SceneObject]]
    frames: list[str]
    frame_indices: Optional[list[int]] = None
    is_video: bool = True
    camera_motions: Optional[list[CameraMotion]] = None
    bbox_format: Literal["normalized_1000", "normalized", "pixels"] = "normalized_1000"


TOOL: dict[str, Any] = {"name": "build_scene", "args": BuildSceneArgs}


def _norm_bbox(bbox: list[float], iw: int, ih: int, bbox_format: str) -> list[float]:
    """Convert explicitly declared coordinates; image resolution never selects the units."""
    x1, y1, x2, y2 = bbox
    if bbox_format == "normalized":
        x1, y1, x2, y2 = [v * 1000.0 for v in bbox]
    elif bbox_format == "pixels":
        x1, y1, x2, y2 = x1 * 1000.0 / iw, y1 * 1000.0 / ih, x2 * 1000.0 / iw, y2 * 1000.0 / ih
    if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
        raise ValueError("bbox must be ordered top-left to bottom-right and within the image")
    return [x1, y1, x2, y2]


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Assemble an instance-level 3D scene from objects YOU (the model) grounded + depth-estimated in each
    frame — NO perception model runs here, it is pure geometry. Returns a `scene` JSON (instances +
    camera poses + intrinsics + frame paths) that visualize_bev / triangulate / object_world_motion /
    calibrate_scale / render_scene_views take back as their `scene` argument. Geometry is a COARSE
    scaffold (depth_m is your estimate), not metric-accurate.

    Args:
        objects_by_frame: Per-frame object lists (outer-model perception). One inner list per frame,
            aligned to `frames`. Each object: {label:str, bbox:[x1,y1,x2,y2] (x-first, top-left origin;
            units selected by bbox_format), depth_m:float (positive distance in meters from the camera)}.
        bbox_format: Coordinate units for every box: normalized_1000 (default), normalized (0..1),
            or pixels. Units are explicit, independent of image resolution.
        frames: Absolute paths to the RGB frame images, aligned 1:1 with objects_by_frame (used for
            image size + carried for render tools).
        frame_indices: Original video frame indices per frame (default: 0..N-1).
        is_video: Whether frames come from a video (vs still images).
        camera_motions: Per adjacent-frame camera motion (frame i→i+1), YOUR estimate. One entry per gap
            = len(frames)-1. Each: {yaw_deg:+right/-left horizontal turn, forward_m:+forward/-back
            planar meters, right_m:+right/-left planar meters, pitch_deg?:+up/-down tilt (recorded, NOT
            used in c2w — BEV is ground-plane 3-DoF)}. Omit → identity cameras (single-view / static).
    """
    if err := require_dep("PIL", "pillow"):
        return err
    from PIL import Image

    try:
        arguments = BuildSceneArgs.model_validate(arguments).model_dump()
    except ValidationError as exc:
        return text_error(str(exc))

    objects_by_frame = arguments["objects_by_frame"]
    frames = arguments.get("frames") or []
    if not frames:
        return text_error("`frames` is required (paths to the RGB frames).")
    if len(objects_by_frame) != len(frames):
        return text_error(f"objects_by_frame ({len(objects_by_frame)}) must align 1:1 with frames ({len(frames)}).")

    frame_indices = arguments["frame_indices"]
    if frame_indices is None:
        frame_indices = list(range(len(frames)))
    if len(frame_indices) != len(frames) or len(set(frame_indices)) != len(frames):
        return text_error("frame_indices must align 1:1 with frames and contain unique indices.")
    motions = arguments["camera_motions"]
    if motions is not None and len(motions) != len(frames) - 1:
        return text_error("camera_motions must contain exactly len(frames)-1 entries.")
    is_video = bool(arguments.get("is_video", True))

    # A scene uses one intrinsic matrix, so all frames must share the same size.
    try:
        with Image.open(frames[0]) as im0:
            W, H = im0.size
        for path in frames[1:]:
            with Image.open(path) as im:
                if im.size != (W, H):
                    return text_error("All frames must have the same image size.")
    except Exception as e:  # noqa: BLE001
        return text_error(f"cannot open frame '{frames[0]}': {e}")

    fx = 0.5 * W / math.tan(math.radians(_SCENE_FOV_DEG) / 2.0)
    fy = fx
    cx, cy = W / 2.0, H / 2.0
    intrinsics = {"fx": float(fx), "fy": float(fy), "cx": float(cx), "cy": float(cy)}

    # --- instances per frame ------------------------------------------------
    instances: dict[str, list[dict]] = {}
    for i, objs in enumerate(objects_by_frame):
        fi = frame_indices[i]
        insts_i: list[dict] = []
        for k, o in enumerate(objs or []):
            try:
                bbox_1000 = _norm_bbox(o["bbox"], W, H, arguments["bbox_format"])
            except ValueError as exc:
                return text_error(f"frame {fi}, object {k}: {exc}")
            x1_k, y1_k, x2_k, y2_k = bbox_1000
            bbox_pixel = [
                round(x1_k * W / 1000.0),
                round(y1_k * H / 1000.0),
                round(x2_k * W / 1000.0),
                round(y2_k * H / 1000.0),
            ]
            label = str(o.get("label", "obj"))
            insts_i.append(
                {
                    "id": f"{label}_{k}",
                    "label": label,
                    "bbox_1000": bbox_1000,
                    "bbox_pixel": bbox_pixel,
                    "point_1000": [(x1_k + x2_k) / 2.0, (y1_k + y2_k) / 2.0],
                    "depth_m": float(o.get("depth_m", 0.0) or 0.0),
                    "conf": float(_OBJ_DEPTH_CONF),
                    "frame_idx": fi,
                }
            )
        instances[str(fi)] = insts_i

    # Accumulate planar motion in the preceding camera's frame (+X right, -Z forward).
    # Pitch is recorded separately; this scene has no full 6-DoF reconstruction.
    pitches = [0.0]
    poses = [(0.0, 0.0, 0.0)]
    cam_source = "camera_motions" if motions is not None else "identity"
    for motion in motions or [{} for _ in frames[1:]]:
        px, pz, yaw = poses[-1]
        angle = math.radians(yaw)
        forward, right = motion.get("forward_m", 0.0), motion.get("right_m", 0.0)
        poses.append(
            (
                px + forward * math.sin(angle) + right * math.cos(angle),
                pz - forward * math.cos(angle) + right * math.sin(angle),
                (yaw + motion.get("yaw_deg", 0.0) + 180.0) % 360.0 - 180.0,
            )
        )
        pitches.append(pitches[-1] + motion.get("pitch_deg", 0.0))

    cameras: dict[str, dict] = {}
    prev_pos = None
    prev_yaw = None
    for i, fi in enumerate(frame_indices):
        pos_x, pos_z, yaw = poses[i]
        if prev_pos is None:
            move_vec = [0.0, 0.0]
            dyaw = 0.0
        else:
            move_vec = [pos_x - prev_pos[0], pos_z - prev_pos[1]]
            dyaw = ((yaw - prev_yaw + 540.0) % 360.0) - 180.0
        cameras[str(fi)] = {
            "frame_idx": fi,
            "pos_bev": [pos_x, pos_z],
            "move_vec_bev": move_vec,
            "yaw_deg": yaw,
            "yaw_delta_deg": dyaw,
            "pitch_deg": pitches[i],
            "roll_deg": 0.0,
            "fov_deg": _SCENE_FOV_DEG,
            "intrinsics": intrinsics,
        }
        prev_pos = (pos_x, pos_z)
        prev_yaw = yaw

    scene = {
        "type": SCENE_SCHEMA,
        "frame_indices": frame_indices,
        "is_video": is_video,
        "metric_scale": 1.0,
        "image_size": {"w": int(W), "h": int(H)},
        "intrinsics": intrinsics,
        "frames": list(frames),
        "instances": instances,
        "cameras": cameras,
    }

    n_inst = sum(len(v) for v in instances.values())
    max_pitch = max((abs(p) for p in pitches), default=0.0)
    pitch_note = (
        f", max |pitch|={max_pitch:.0f}° (BEV may be unreliable, prefer monocular Pattern A)"
        if max_pitch >= 20.0
        else ""
    )
    summary = (
        f"Built scene: {len(frame_indices)} frame(s), {n_inst} instance(s), "
        f"{W}x{H}, FOV={_SCENE_FOV_DEG:.0f}°, cameras={cam_source}{pitch_note}. "
        f"Pass `scene` below to visualize_bev / triangulate / object_world_motion / calibrate_scale."
    )
    return [text(summary), json_text(scene)]
