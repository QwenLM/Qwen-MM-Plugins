"""Scene expert: structured scene-level outputs (cognitive map / grid layout).

Fills capability class 5 (空间想象 → 认知地图/cogmap_spatial): produce a
top-down grid "cognitive map" of object positions in the room, matching the
CogMap JSON format ({grid_size, objects:[{name, position:[gx, gy]}]}).

Model-free: reasons ENTIRELY over the instance-level reconstruction plus the
base VLM. Object placement uses ``recon.get_instances`` (bbox center + depth in
meters → bird's-eye XZ via the camera FOV/pose), ``recon.get_camera`` for the
camera pose, and ``vlm.ask_with_thinking`` for an optional holistic scene
description. No dense reconstruction, segmentation, or external perception is
used. Public method names and returned dict keys are preserved so callers are
unchanged. The VLM handle is injected via ``set_vlm_module``.
"""

import math
from typing import Any, Dict, List, Optional

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class SceneExpert(CPUTool):
    """Scene-level structured reasoning (cognitive map / grid layout).

    Builds top-down layouts from the model-free ``recon`` instance scene.
    The VLM handle is injected via ``set_vlm_module`` (mirrors Reconstruct).
    """

    TOOL_PROMPT_DESCRIPTION = """\
### tools.Scene — Scene Structure / Cognitive Map Expert (CPU)

Builds a structured top-down "cognitive map" of the scene: each object placed on
an N×N grid by its bird's-eye position, derived from `recon.get_instances`
(bbox center + distance in meters, projected through the camera FOV/pose). Use it
for layout / mental-map questions ("what is to the left of the sofa on the map").

| Method | Signature | Returns |
|--------|-----------|---------|
| `build_cognitive_map` | `(recon, frame=None, grid_size=10, labels=None, describe=False)` | grid layout JSON |
| `appearance_order` | `(recon)` | objects ordered by first-appearance frame |
| `locate_event` | `(recon, images, description, frames=None)` | frames where an event/action occurs (temporal localization) |
| `diff_frames` | `(recon, frame_a, frame_b, images=None)` | what appeared / disappeared / moved between two frames |

**`build_cognitive_map` output** (CogMap format):
```python
{"grid_size": [10, 10],
 "objects": [
   {"name": "sofa", "position": [3, 5], "xy_m": [1.2, 2.3]},
   {"name": "table", "position": [4, 5], "xy_m": [1.6, 2.4]}
 ],
 "extent_m": {"x": [-2.1, 2.6], "z_forward": [0.0, 4.2]}}
```
`position` = grid cell [col=right, row=forward] (row 0 = nearest to camera);
`xy_m` = metric (x_right, forward) in meters.

**Example**
```python
cmap = tools.Scene.build_cognitive_map(recon, frame=recon.frame_indices[-1])
for o in cmap["objects"]:
    print(o["name"], "at grid", o["position"])

# Two coordinates on the map → who is left/right:
show(recon.bev_visual(ref_frame=recon.frame_indices[-1], show_instances=True))

# Optional holistic description via the base model:
cmap = tools.Scene.build_cognitive_map(recon, describe=True)
print(cmap.get("description"))

order = tools.Scene.appearance_order(recon)      # scene 出现顺序
print(order["order"])
```
"""

    def __init__(self):
        self._vlm_module = None
        self._tracer = None

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    # Instance-level BEV helpers (mirror mobile_expert)
    # ------------------------------------------------------------------

    @staticmethod
    def _fov_deg(cam: Optional[dict]) -> float:
        if cam and cam.get("fov_deg"):
            return float(cam["fov_deg"])
        return 60.0

    @staticmethod
    def _inst_angle_deg(inst: dict, cam: Optional[dict]) -> float:
        """Horizontal bearing of an instance from camera forward, deg (+right)."""
        px = float(inst.get("point_1000", [500.0, 500.0])[0])
        frac = px / 1000.0 - 0.5
        return frac * SceneExpert._fov_deg(cam)

    @staticmethod
    def _inst_bev_xz(inst: dict, cam: Optional[dict]) -> tuple:
        """World bird's-eye (x_right, z_forward) meters of an instance.

        Ego frame: z = forward = distance*cos(bearing), x = right =
        distance*sin(bearing); rotated by camera yaw and translated by
        ``pos_bev`` into the world frame.
        """
        dist = float(inst.get("depth_m", 0.0) or 0.0)
        ang = math.radians(SceneExpert._inst_angle_deg(inst, cam))
        ego_x = dist * math.sin(ang)
        ego_z = dist * math.cos(ang)
        if not cam:
            return (ego_x, ego_z)
        yaw = math.radians(float(cam.get("yaw_deg", 0.0)))
        px, pz = cam.get("pos_bev", (0.0, 0.0))
        world_x = float(px) + ego_z * math.sin(yaw) + ego_x * math.cos(yaw)
        world_z = float(pz) - ego_z * math.cos(yaw) + ego_x * math.sin(yaw)
        return (world_x, world_z)

    @staticmethod
    def _resolve_frame(recon, frame: Optional[int]) -> int:
        if frame is not None:
            return int(frame)
        fis = getattr(recon, "frame_indices", None) or list((recon.instances or {}).keys())
        return int(fis[-1]) if fis else 0

    def _frame_image(self, recon, frame: int):
        """Best-effort PIL image for a frame (for the VLM description)."""
        fis = list(getattr(recon, "frame_indices", []) or [])
        stored = getattr(recon, "_input_images", None)
        if stored:
            if frame in fis and fis.index(frame) < len(stored):
                return stored[fis.index(frame)]
            return stored[0]
        return None

    # ------------------------------------------------------------------
    # Cognitive map
    # ------------------------------------------------------------------

    def build_cognitive_map(
        self,
        recon,
        frame: Optional[int] = None,
        grid_size: int = 10,
        labels: Optional[List[str]] = None,
        describe: bool = False,
    ) -> Dict[str, Any]:
        """Place each object on a top-down grid by its bird's-eye position.

        Positions come from ``recon.get_instances(frame)``: each instance's bbox
        center + distance (meters) is projected through the camera FOV/pose to a
        world (x_right, forward) pair. The map plane is (x_right, forward), so
        row 0 (top of grid) is nearest the camera / smallest forward. When
        ``describe`` is set and a VLM is injected, a holistic scene sentence from
        ``vlm.ask_with_thinking`` is added under ``description``.
        """
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        insts = recon.get_instances(frame) or []

        items = []
        for it in insts:
            name = it.get("label", "object")
            if labels is not None and name not in labels:
                continue
            x_right, forward = self._inst_bev_xz(it, cam)
            if not (math.isfinite(x_right) and math.isfinite(forward)):
                continue
            items.append({"name": name, "x": float(x_right), "f": float(forward)})

        if not items:
            out = {
                "grid_size": [grid_size, grid_size],
                "objects": [],
                "extent_m": None,
                "note": "no objects with a valid position",
            }
            if describe:
                out["description"] = self._describe_scene(recon, frame)
            return out

        xs = np.array([it["x"] for it in items])
        fs = np.array([it["f"] for it in items])
        xmin, xmax = float(xs.min()), float(xs.max())
        fmin, fmax = float(fs.min()), float(fs.max())
        xr = max(xmax - xmin, 1e-6)
        fr = max(fmax - fmin, 1e-6)

        objects = []
        for it in items:
            gx = int(round((it["x"] - xmin) / xr * (grid_size - 1)))
            gy = int(round((it["f"] - fmin) / fr * (grid_size - 1)))
            gx = max(0, min(grid_size - 1, gx))
            gy = max(0, min(grid_size - 1, gy))
            objects.append(
                {
                    "name": it["name"],
                    "position": [gx, gy],
                    "xy_m": [round(it["x"], 2), round(it["f"], 2)],
                }
            )

        out = {
            "grid_size": [grid_size, grid_size],
            "objects": objects,
            "extent_m": {"x": [round(xmin, 2), round(xmax, 2)], "z_forward": [round(fmin, 2), round(fmax, 2)]},
        }
        if describe:
            out["description"] = self._describe_scene(recon, frame)
        return out

    def _describe_scene(self, recon, frame: int) -> Optional[str]:
        """Holistic one-paragraph scene description via the base VLM (optional)."""
        if self._vlm_module is None:
            return None
        img = self._frame_image(recon, frame)
        if img is None:
            return None
        try:
            return self._vlm_module.ask_with_thinking(
                img,
                "Describe the room's spatial layout: the main objects/furniture and "
                "where they are relative to each other and the camera.",
            )
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Appearance order
    # ------------------------------------------------------------------

    def appearance_order(self, recon) -> Dict[str, Any]:
        """Order objects by the FIRST frame each appears (scene 出现顺序).

        Scans ``recon.get_instances`` across ``recon.frame_indices``; for each
        distinct object label, records the first frame it is present, then
        returns labels ordered by first appearance (absolute frame index).
        """
        fis = list(getattr(recon, "frame_indices", []) or sorted((recon.instances or {}).keys()))
        first_seen: Dict[str, int] = {}
        for fi in fis:
            for it in recon.get_instances(fi) or []:
                name = it.get("label", "object")
                if name not in first_seen:
                    first_seen[name] = int(fi)
        order = [{"obj": name, "first_frame": f} for name, f in first_seen.items()]
        order.sort(key=lambda x: x["first_frame"])
        return {"order": [o["obj"] for o in order], "details": order}

    # ------------------------------------------------------------------
    # Temporal reasoning (ADDITIVE): event localization + change detection
    # ------------------------------------------------------------------

    @staticmethod
    def _frame_img(recon, images, f: int):
        """Best-effort PIL image for frame ``f`` (mirrors mobile_expert._frame_image).

        Accepts an optional ``images`` list (aligned to ``recon.frame_indices``);
        falls back to ``recon._input_images``.
        """

        def _plain(x):
            return x.image if hasattr(x, "image") else x

        fis = list(getattr(recon, "frame_indices", []) or [])
        if images:
            imgs = list(images)
            if f in fis:
                loc = fis.index(f)
                if loc < len(imgs):
                    return _plain(imgs[loc])
            return _plain(imgs[0])
        stored = getattr(recon, "_input_images", None)
        if stored:
            if f in fis and fis.index(f) < len(stored):
                return stored[fis.index(f)]
            return stored[0]
        return None

    def locate_event(self, recon, images, description: str, frames: Optional[List[int]] = None) -> Dict[str, Any]:
        """Temporal localization: which frames show ``description``.

        For each frame (``frames`` or ``recon.frame_indices``) the aligned image is
        asked ``vlm.ask(img, "Does this frame show: <description>? Answer yes or
        no.")``; frames answering yes are collected. Returns the hit frames plus the
        first/last hit and a count.
        """
        if self._vlm_module is None:
            return {"error": "no vlm"}
        fis = (
            list(frames)
            if frames is not None
            else list(getattr(recon, "frame_indices", []) or sorted((recon.instances or {}).keys()))
        )
        hit_frames: List[int] = []
        for f in fis:
            img = self._frame_img(recon, images, f)
            if img is None:
                continue
            try:
                ans = self._vlm_module.ask(img, f"Does this frame show: {description}? Answer yes or no.")
            except Exception:  # noqa: BLE001
                continue
            if ans and "yes" in ans.strip().lower():
                hit_frames.append(int(f))
        out: Dict[str, Any] = {
            "description": description,
            "hit_frames": hit_frames,
            "first_frame": hit_frames[0] if hit_frames else None,
            "last_frame": hit_frames[-1] if hit_frames else None,
            "n_hit": len(hit_frames),
        }
        if not hit_frames:
            out["note"] = "no frame matched"
        return out

    def diff_frames(self, recon, frame_a: int, frame_b: int, images=None, use_vlm: bool = True) -> Dict[str, Any]:
        """Change detection between two frames by comparing instance labels.

        Labels only in ``frame_b`` → "appeared"; only in ``frame_a`` →
        "disappeared". For labels common to both, the bbox-center shift (0-1000) and
        ``depth_m`` change are reported under "moved". When ``use_vlm`` and images
        are available, a holistic ``vlm.ask_with_thinking`` summary is added.
        """
        insts_a = recon.get_instances(frame_a) or []
        insts_b = recon.get_instances(frame_b) or []

        def _by_label(insts):
            d: Dict[str, dict] = {}
            for it in insts:
                lab = it.get("label", "object")
                if lab not in d:
                    d[lab] = it
            return d

        map_a = _by_label(insts_a)
        map_b = _by_label(insts_b)
        labels_a = set(map_a.keys())
        labels_b = set(map_b.keys())

        appeared = sorted(labels_b - labels_a)
        disappeared = sorted(labels_a - labels_b)

        def _center(it):
            bb = it.get("bbox_1000") or [0.0, 0.0, 0.0, 0.0]
            return ((float(bb[0]) + float(bb[2])) / 2.0, (float(bb[1]) + float(bb[3])) / 2.0)

        moved = []
        for lab in sorted(labels_a & labels_b):
            ax, ay = _center(map_a[lab])
            bx, by = _center(map_b[lab])
            d_center = math.hypot(bx - ax, by - ay)
            da = float(map_a[lab].get("depth_m", 0.0) or 0.0)
            db = float(map_b[lab].get("depth_m", 0.0) or 0.0)
            moved.append(
                {
                    "label": lab,
                    "d_center_1000": round(d_center, 2),
                    "d_depth_m": round(db - da, 3),
                }
            )

        out: Dict[str, Any] = {
            "frame_a": int(frame_a),
            "frame_b": int(frame_b),
            "appeared": appeared,
            "disappeared": disappeared,
            "moved": moved,
        }
        if use_vlm and self._vlm_module is not None:
            img_a = self._frame_img(recon, images, frame_a)
            img_b = self._frame_img(recon, images, frame_b)
            if img_a is not None and img_b is not None:
                try:
                    out["vlm_summary"] = self._vlm_module.ask_with_thinking(
                        [img_a, img_b],
                        "What changed between these two frames (objects that moved/"
                        "appeared/disappeared/changed state)?",
                    )
                except Exception:  # noqa: BLE001
                    out["vlm_summary"] = None
        return out

    def __repr__(self) -> str:
        return "SceneExpert(methods: build_cognitive_map, appearance_order)"
