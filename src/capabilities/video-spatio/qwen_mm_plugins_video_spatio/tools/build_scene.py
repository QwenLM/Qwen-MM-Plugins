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
from typing import Any, Optional

from pydantic import BaseModel, Field

from shared.content import json_text, require_dep, text, text_error

_SCENE_FOV_DEG = 60.0
_OBJ_DEPTH_CONF = 1.0
SCENE_SCHEMA = "video-spatio/scene@1"


class BuildSceneArgs(BaseModel):
    objects_by_frame: list[list[dict]] = Field(
        description=(
            "Per-frame object lists (outer-model perception). One inner list per frame, aligned to "
            "`frames`. Each object: {label:str, bbox:[x1,y1,x2,y2] (x-first, top-left origin; 0-1 / "
            "0-1000 / pixels auto-detected), depth_m:float (meters, from the camera)}."
        )
    )
    frames: list[str] = Field(
        description="Absolute paths to the RGB frame images, aligned 1:1 with objects_by_frame (used for image size + carried for render tools)."
    )
    frame_indices: Optional[list[int]] = Field(
        default=None, description="Original video frame indices per frame (default: 0..N-1)."
    )
    is_video: bool = Field(default=True, description="Whether frames come from a video (vs still images).")
    camera_motions: Optional[list[dict]] = Field(
        default=None,
        description=(
            "Per adjacent-frame camera motion (frame i→i+1), YOUR estimate. One entry per gap = "
            "len(frames)-1. Each: {yaw_deg:+right/-left horizontal turn, forward_m:+forward/-back "
            "planar meters, right_m:+right/-left planar meters, pitch_deg?:+up/-down tilt (recorded, "
            "NOT used in c2w — BEV is ground-plane 3-DoF)}. Omit → identity cameras (single-view / static)."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "build_scene",
    "description": (
        "Assemble an instance-level 3D scene from objects YOU (the model) grounded + depth-estimated "
        "in each frame — NO perception model runs here, it is pure geometry. Returns a `scene` JSON "
        "(instances + camera poses + intrinsics + frame paths) that visualize_bev / triangulate / "
        "object_world_motion / calibrate_scale / render_scene_views take back as their `scene` argument. "
        "Geometry is a COARSE scaffold (depth_m is your estimate), not metric-accurate."
    ),
    "args": BuildSceneArgs,
}


def _norm_bbox(bbox: list[float], iw: int, ih: int) -> list[float]:
    """Normalize a [x1,y1,x2,y2] bbox to the 0..1000 (TL-BR) convention (mirrors Reconstruct)."""
    x1, y1, x2, y2 = bbox
    mx = max(x1, y1, x2, y2)
    if mx <= 1.0:  # 0..1 normalized
        x1, y1, x2, y2 = x1 * 1000.0, y1 * 1000.0, x2 * 1000.0, y2 * 1000.0
    elif mx <= 1000.0 and (iw < 1000 or ih < 1000):  # already 0..1000
        pass
    else:  # raw pixels → 0..1000
        x1, y1 = x1 * 1000.0 / iw, y1 * 1000.0 / ih
        x2, y2 = x2 * 1000.0 / iw, y2 * 1000.0 / ih
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    clamp = lambda v: max(0.0, min(1000.0, float(v)))  # noqa: E731
    return [clamp(x1), clamp(y1), clamp(x2), clamp(y2)]


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    if err := require_dep("PIL", "pillow"):
        return err
    from PIL import Image

    objects_by_frame = arguments.get("objects_by_frame") or []
    frames = arguments.get("frames") or []
    if not frames:
        return text_error("`frames` is required (paths to the RGB frames).")
    if len(objects_by_frame) != len(frames):
        return text_error(f"objects_by_frame ({len(objects_by_frame)}) must align 1:1 with frames ({len(frames)}).")

    frame_indices = arguments.get("frame_indices") or list(range(len(frames)))
    frame_indices = [int(x) for x in frame_indices[: len(frames)]]
    is_video = bool(arguments.get("is_video", True))

    # Image size from the first frame (assumed uniform, as in Reconstruct).
    try:
        with Image.open(frames[0]) as im0:
            W, H = im0.size
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
            bbox = o.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            bbox_1000 = _norm_bbox([float(v) for v in bbox], W, H)
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

    # --- cameras: real 3-DoF poses when camera_motions is provided (yaw + planar translation),
    #     else identity (single-view / static). pitch_deg is recorded in meta.pitch_deg per frame
    #     BUT NOT baked into c2w — BEV geometry uses only ground-plane 3-DoF (like the old flow).
    N = len(frame_indices)
    pitches = [0.0] * N
    cam_source = "identity"
    poses = [(0.0, 0.0, 0.0)] * N  # (pos_x_m, pos_z_m, yaw_deg) per frame
    motions_in = arguments.get("camera_motions") or None
    if motions_in and N > 1:
        try:
            need = N - 1
            got = list(motions_in)[:need]
            if len(got) < need:  # pad with zero-motion
                got = got + [{"yaw_deg": 0.0, "forward_m": 0.0, "right_m": 0.0}] * (need - len(got))
            import numpy as np

            from qwen_mm_plugins_video_spatio.camera_poses import _pose_from_motion

            rels = [
                _pose_from_motion(
                    float(m.get("yaw_deg") or 0.0), float(m.get("forward_m") or 0.0), float(m.get("right_m") or 0.0)
                )
                for m in got
            ]
            # accumulate raw 3-DoF rel transforms (cam_i in cam-0 local coords). World convention is
            # +X right, -Z forward (see _pose_from_motion: t=[right,0,-forward]); +yaw = turned RIGHT.
            # This is what the scene's pos_bev + yaw_deg use directly (validated vs synthetic GT).
            cum = [np.eye(4)]
            for r in rels:
                cum.append(cum[-1] @ r)
            for i in range(N):
                M = cum[i]
                # world coords: pos_bev = (x, z) in the cam-0 local frame; +X right, -Z forward
                pos_x = float(M[0, 3])
                pos_z = float(M[2, 3])
                fwd = M[:3, :3] @ np.array([0.0, 0.0, 1.0])  # cam-space forward
                # _pose_from_motion negates yaw in the rotation, so undo it here so scene.yaw_deg
                # keeps the SKILL convention: +yaw = camera turned RIGHT (about +Y up).
                yaw = -math.degrees(math.atan2(float(fwd[0]), float(fwd[2])))
                poses[i] = (pos_x, pos_z, yaw)
                pitches[i] = float((got[i - 1].get("pitch_deg") if i > 0 else 0.0) or 0.0) if i > 0 else 0.0
            # cumulative pitch (informational — not in c2w)
            cum_pitch = 0.0
            for i in range(N):
                if i > 0:
                    cum_pitch += float((got[i - 1].get("pitch_deg") or 0.0))
                pitches[i] = cum_pitch
            cam_source = "camera_motions"
        except Exception as e:  # noqa: BLE001
            cam_source = f"identity (motions parse failed: {type(e).__name__})"
            poses = [(0.0, 0.0, 0.0)] * N
            pitches = [0.0] * N

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
