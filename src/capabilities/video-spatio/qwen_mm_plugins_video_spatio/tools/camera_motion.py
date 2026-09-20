"""MCP tool: camera_motion — translation + rotation of the camera between two frames (pure compute)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class CameraMotionArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    frame_i: int
    frame_j: int


TOOL: dict[str, Any] = {"name": "camera_motion", "args": CameraMotionArgs}


def _wrap180(a: float) -> float:
    return ((a + 540.0) % 360.0) - 180.0


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    # Pure geometry from the scene's stored 3-DoF camera poses (pos_bev + yaw_deg + pitch_deg).
    # Model-free path: scene_to_recon leaves extrinsics=None, so we do NOT go through CameraExpert
    # (which indexes recon.extrinsics and would crash). Same world convention as build_scene /
    # visualize_bev: +X = right, -Z = forward, +yaw = turned RIGHT. Validated against synthetic GT.
    """How the CAMERA moved between two frames, from the scene's camera poses (pure geometry): translation
    (meters), rotation (yaw/pitch/roll deg), total rotation, and dominant direction (e.g. yaw-left,
    moved-forward). Answers 'which way did the camera move/turn?'. Needs a `scene`.

    Args:
        scene: The `scene` object from build_scene (JSON object or JSON string).
        scene_file: Path to a JSON file holding the scene (alternative to `scene`).
        frame_i: First frame index.
        frame_j: Second frame index.
    """
    import math

    try:
        scene = _scene.load_scene(arguments)
        cams = scene.get("cameras") or {}
        i, j = int(arguments["frame_i"]), int(arguments["frame_j"])
        ci, cj = cams.get(str(i)), cams.get(str(j))
        if ci is None or cj is None:
            return text_error(f"camera_motion: frame {i if ci is None else j} not in scene.cameras")

        pi = ci.get("pos_bev") or [0.0, 0.0]
        pj = cj.get("pos_bev") or [0.0, 0.0]
        yaw_i = float(ci.get("yaw_deg") or 0.0)
        yaw_j = float(cj.get("yaw_deg") or 0.0)
        pitch_i = float(ci.get("pitch_deg") or 0.0)
        pitch_j = float(cj.get("pitch_deg") or 0.0)

        # world translation, decomposed into frame_i's (right, forward) axes (H = yaw_i)
        dx, dz = float(pj[0]) - float(pi[0]), float(pj[1]) - float(pi[1])
        H = math.radians(yaw_i)
        cH, sH = math.cos(H), math.sin(H)
        t_right = cH * dx + sH * dz  # + = moved RIGHT
        t_fwd = sH * dx - cH * dz  # + = moved FORWARD  (matches visualize_bev's forward axis)
        dist = math.hypot(dx, dz)

        yaw_delta = _wrap180(yaw_j - yaw_i)  # + = turned RIGHT
        pitch_delta = pitch_j - pitch_i

        tcomps = {"right": t_right, "left": -t_right, "forward": t_fwd, "backward": -t_fwd}
        dom_t = max(tcomps, key=tcomps.get) if dist > 1e-4 else "none"
        if abs(yaw_delta) < 0.5 and abs(pitch_delta) < 0.5:
            dom_r = "none"
        elif abs(yaw_delta) >= abs(pitch_delta):
            dom_r = "yaw-right" if yaw_delta > 0 else "yaw-left"
        else:
            dom_r = "pitch-up" if pitch_delta > 0 else "pitch-down"

        result = {
            "frame_i": i,
            "frame_j": j,
            "translation_m": {"right": round(t_right, 3), "forward": round(t_fwd, 3), "distance": round(dist, 3)},
            "rotation_deg": {"yaw": round(yaw_delta, 2), "pitch": round(pitch_delta, 2)},
            "dominant_translation": dom_t,
            "dominant_rotation": dom_r,
            "note": "yaw +right/-left, forward=-Z, right=+X (same frame as visualize_bev).",
        }
    except Exception as e:  # noqa: BLE001
        return text_error(f"camera_motion failed: {e}")
    return [json_text(result)]
