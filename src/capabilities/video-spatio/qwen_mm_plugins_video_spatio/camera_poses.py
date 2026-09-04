"""Camera-motion estimation for PROMPT mode (model-free).

Prompt mode has no SLAM/reconstruction model, so ReconstructPromptTool otherwise
places every frame's camera at the origin with identical orientation — which
collapses camera motion and makes CROSS-IMAGE spatial relations wrong (objects
seen in different frames get glued into one frame as if the camera never moved).

This module asks the base VLM to estimate the coarse relative camera pose between
consecutive frames (yaw about vertical + planar translation), so objects from
different frames can be placed in a common frame anchored to camera 0, and the BEV
can draw each camera at its estimated pose. Coarse but far better than zero-motion.

Convention (matches per_frame_types BEV / ReconstructPromptTool):
  world = camera-0 frame; camera looks -Z, +Y up, +X right.
  yaw_deg > 0 means the camera TURNED RIGHT between the two frames.
"""

import json
import logging
import math
import re
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# camera-0 base c2w (camera looks -Z, +Y up) — same as ReconstructPromptTool
BASE_C2W = np.diag([1.0, -1.0, -1.0, 1.0])

_MOTION_PROMPT = """\
These are two frames (A then B) from the SAME scene, camera possibly moved between them.
Estimate how the CAMERA moved from A to B (not the objects). Reply ONLY compact JSON:
{"yaw_deg": <+right / -left horizontal turn, degrees>, "forward_m": <+forward / -back meters>, "right_m": <+right / -left meters>, "pitch_deg": <+up / -down tilt, degrees>, "confidence": <0..1>}\nyaw_deg/forward_m/right_m describe motion ON THE GROUND PLANE (used for the top-down map). pitch_deg is only how the camera tilted up/down.
Use 0 for an axis you cannot tell. Keep magnitudes physically plausible for an indoor walkthrough."""


def _yaw_deg_to_R(yaw_deg: float) -> np.ndarray:
    """Rotation about +Y (vertical). +yaw = turn RIGHT (forward -Z rotates toward +X)."""
    th = math.radians(-yaw_deg)  # sign: +yaw_deg(right) => forward -Z -> +X
    c, s = math.cos(th), math.sin(th)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _pose_from_motion(yaw_deg: float, forward_m: float, right_m: float) -> np.ndarray:
    """4x4 rigid transform (camera_i -> camera_0 world) from coarse motion."""
    T = np.eye(4)
    T[:3, :3] = _yaw_deg_to_R(yaw_deg)
    T[:3, 3] = np.array([right_m, 0.0, -forward_m])  # +X right, -Z forward
    return T


def _parse_motion(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        d = json.loads(m.group(0) if m else text)
    except Exception:  # noqa: BLE001
        return None
    out = {}
    for k, dv in (("yaw_deg", 0.0), ("forward_m", 0.0), ("right_m", 0.0), ("pitch_deg", 0.0), ("confidence", 0.5)):
        try:
            out[k] = float(d.get(k, dv))
        except (TypeError, ValueError):
            out[k] = dv
    return out


def estimate_camera_motions(frames: List, vlm_module) -> np.ndarray:
    """Return (N,4,4) rigid motions M_i mapping frame-i's (BASE-flipped) points
    into camera-0's world frame. M_0 = identity. Motions accumulate consecutive
    A->B estimates. On any failure a step is identity (graceful -> no-motion).

    Points transform:  p_world = M_i[:3,:3] @ p_cam_i + M_i[:3,3]
    Extrinsics (c2w) for the BEV renderer:  motions_to_c2w(M_i)
    """
    N = len(frames)
    motions = np.tile(np.eye(4), (N, 1, 1)).astype(np.float64)
    yaw_notes = []
    pitch_notes = []
    if N <= 1 or vlm_module is None:
        return motions, ""
    accum = np.eye(4)
    for i in range(1, N):
        rel = np.eye(4)
        try:
            from PIL import Image

            a = frames[i - 1].convert("RGB")
            b = frames[i].convert("RGB")
            h = max(a.height, b.height)
            canvas = Image.new("RGB", (a.width + b.width + 12, h), (255, 255, 255))
            canvas.paste(a, (0, 0))
            canvas.paste(b, (a.width + 12, 0))
            mo = _parse_motion(vlm_module.ask_with_thinking(canvas, _MOTION_PROMPT))
            if mo is not None:
                # BEV geometry uses ONLY the ground-plane 3-DoF (yaw + planar translation).
                rel = _pose_from_motion(mo["yaw_deg"], mo["forward_m"], mo["right_m"])
                if abs(mo["yaw_deg"]) >= 5.0:
                    d = "right" if mo["yaw_deg"] > 0 else "left"
                    yaw_notes.append(f"frame {i}: camera turned {d} ~{abs(mo['yaw_deg']):.0f} deg vs frame {i - 1}")
                if abs(mo["pitch_deg"]) >= 5.0:
                    d = "up" if mo["pitch_deg"] > 0 else "down"
                    pitch_notes.append(f"frame {i}: camera tilted {d} ~{abs(mo['pitch_deg']):.0f} deg vs frame {i - 1}")
                logger.info(
                    "[cam-motion] %d<-%d yaw=%.0f fwd=%.2f right=%.2f pitch=%.0f",
                    i,
                    i - 1,
                    mo["yaw_deg"],
                    mo["forward_m"],
                    mo["right_m"],
                    mo["pitch_deg"],
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("[cam-motion] step %d failed: %s -> no motion", i, e)
        accum = accum @ rel
        motions[i] = accum
    parts = []
    if yaw_notes:
        parts.append("Camera turn (yaw, on the top-down map; +right/-left): " + "; ".join(yaw_notes))
    if pitch_notes:
        parts.append("Camera tilt (elevation, not shown on the top-down map): " + "; ".join(pitch_notes))
    notes = " | ".join(parts)
    return motions, notes


def motions_to_c2w(motions: np.ndarray) -> np.ndarray:
    """Convert motions (cam_i->cam0) to c2w extrinsics for the BEV renderer."""
    return np.stack([m @ BASE_C2W for m in motions])


if __name__ == "__main__":  # self-test on the chair/lamp demo geometry

    def bp(u, v, d, W=640, FOV=60.0):
        fx = (W / 2) / math.tan(math.radians(FOV / 2))
        cx, cy = W / 2, 240
        return np.array([(u - cx) * d / fx, -(v - cy) * d / fx, -d])

    def bev(p):
        return np.array([p[0], -p[2]])

    T1 = _pose_from_motion(40, 0, 0)  # camera turned RIGHT 40
    lamp = bp(460, 240, 3.5)
    lamp_w = (T1[:3, :3] @ lamp) + T1[:3, 3]
    print("lamp BEV (right,forward):", np.round(bev(lamp_w), 2), "-> right should be > 0")
    fwd1 = T1[:3, :3] @ np.array([0, 0, -1])
    print("cam1 forward BEV:", np.round(bev(fwd1), 2), "-> right>0 means turned right (correct)")
