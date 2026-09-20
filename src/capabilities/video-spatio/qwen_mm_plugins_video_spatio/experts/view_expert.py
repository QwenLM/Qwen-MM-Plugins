"""View expert (model-free): object-centric viewpoint reasoning.

Reasons about how the scene looks from a chosen object's point of view using ONLY
the instance-level, model-free reconstruction plus the base model:

- ``recon.get_instances(frame)`` -> per-frame ``{id, label, bbox_1000, point_1000,
  depth_m, ...}``.
- ``recon.get_camera(frame)``    -> per-frame ``{pos_bev, yaw_deg, fov_deg, ...}``.
- ``vlm.ask`` / ``vlm.ask_with_thinking`` on the frame image for perceptual
  visibility / left-right-front-behind judgements.

No dense reconstruction, segmentation mask, or camera-extrinsics matrix is used. An
object's position is derived from its bbox center + ``depth_m`` via the FOV bearing /
ego math (see ``_inst_bev_xz``). Public method names and returned dict field names
are preserved so callers are unchanged. The base-model handle is injected via
``set_vlm_module`` (mirrors Reconstruct), so methods do not take a ``vlm`` argument.
"""

import math
from typing import Any, Dict, List, Optional, Union

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class ViewExpert(CPUTool):
    """Object-centric spatial reasoning from a specific viewpoint (model-free).

    Computes how other objects appear from a chosen object's perspective,
    supporting person-perspective questions in ViewSpatial-Bench.
    """

    TOOL_PROMPT_DESCRIPTION = """\
### tools.View - Object-Centric View Expert (CPU, model-free)

Reasons about spatial layout from a specific object's viewpoint (e.g. "from the
person's point of view, is the table to their left or right?"). Geometry comes from
the instance-level reconstruction; left/right/front/behind wording is confirmed by
the base model on the frame image.

**Prerequisites**: You need `recon` (from `tools.Reconstruct.Reconstruct(frames)`).
Optionally pass `images` (the frames) so the base model can confirm the viewpoint's
facing direction.

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `from_viewpoint` | `(recon, frame, viewpoint, targets, images=None)` | `list[dict]` | Describe all targets relative to the viewpoint object |
| `visible_from` | `(recon, frame, viewpoint, target, images=None)` | `dict` | Is target visible from viewpoint + its direction/distance |
| `scene_layout` | `(recon, frame, viewpoint, images=None)` | `dict` | Full layout of all objects from viewpoint's perspective |
| `line_of_sight` | `(recon, frame, viewpoint, target, images=None)` | `dict` | Is the viewpoint→target line clear or occluded by other objects |

`viewpoint` / `target` may be a label string (e.g. `"person"`) or an instance index.
Each result dict carries `direction` (e.g. "to the left, in front"), `distance_m`
(straight-line distance in meters), `angle_deg`, and `local_xyz`.

**Example - from the person's viewpoint, where are all objects?**
```python
layout = tools.View.scene_layout(recon, frame=fi, viewpoint="person", images=InputImages)
for obj in layout["objects"]:
    print(f"  {obj['label']}: {obj['direction']} at {obj['distance_m']:.1f}m")
```

**Example - from the sofa, is the TV to the left or right?**
```python
result = tools.View.visible_from(recon, frame=fi, viewpoint="sofa", target="tv",
                                 images=InputImages)
print(result["direction"])   # "to the left"
```

Geometry is a coarse estimate; for the final answer prefer confirming with
`vlm.ask_with_thinking(image, question)`.
"""

    # ------------------------------------------------------------------

    def __init__(self):
        self._vlm_module = None
        self._tracer = None

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    # Instance-level geometry helpers (mirror mobile_expert)
    # ------------------------------------------------------------------

    @staticmethod
    def _fov_deg(cam: Optional[dict]) -> float:
        if cam and cam.get("fov_deg"):
            return float(cam["fov_deg"])
        return 60.0

    @staticmethod
    def _inst_angle_deg(inst: dict, cam: Optional[dict]) -> float:
        """Horizontal bearing of an instance from camera forward (deg, +right)."""
        px = float(inst.get("point_1000", [500.0, 500.0])[0])
        frac = px / 1000.0 - 0.5
        return frac * ViewExpert._fov_deg(cam)

    @staticmethod
    def _inst_bev_xz(inst: dict, cam: Optional[dict]) -> tuple:
        """World BEV (x, z) meters of an instance, using the camera pose.

        Ego frame: z = forward = depth*cos(bearing), x = right = depth*sin(bearing);
        rotated by camera yaw and translated by camera ``pos_bev`` into world XZ.
        """
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        ang = math.radians(ViewExpert._inst_angle_deg(inst, cam))
        ego_x = depth * math.sin(ang)  # right
        ego_z = depth * math.cos(ang)  # forward
        if not cam:
            return (ego_x, ego_z)
        yaw = math.radians(float(cam.get("yaw_deg", 0.0)))
        px, pz = cam.get("pos_bev", (0.0, 0.0))
        world_x = float(px) + ego_z * math.sin(yaw) + ego_x * math.cos(yaw)
        world_z = float(pz) - ego_z * math.cos(yaw) + ego_x * math.sin(yaw)
        return (world_x, world_z)

    @staticmethod
    def _inst_vertical_m(inst: dict, cam: Optional[dict]) -> float:
        """Rough world-vertical offset (m) of an instance from the camera height.

        Uses the bbox-center vertical fraction (``point_1000[1]``, +down) and the
        camera pitch as a vertical bearing, scaled by ``depth_m``. Only relative
        vertical ordering (above / below) is meaningful.
        """
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        py = float(inst.get("point_1000", [500.0, 500.0])[1]) / 1000.0
        v_bearing = (0.5 - py) * ViewExpert._fov_deg(cam)  # +up
        pitch = float(cam.get("pitch_deg", 0.0)) if cam else 0.0
        return depth * math.tan(math.radians(v_bearing + pitch))

    @staticmethod
    def _delta_to_direction(dx: float, dy: float, dz: float, threshold: float = 0.15) -> str:
        """Convert viewpoint-local displacements to a natural-language direction."""
        parts = []
        if abs(dx) > threshold:
            parts.append("to the right" if dx > 0 else "to the left")
        if abs(dz) > threshold:
            parts.append("in front" if dz > 0 else "behind")
        if abs(dy) > threshold:
            parts.append("above" if dy > 0 else "below")
        return ", ".join(parts) if parts else "at the same position"

    # ------------------------------------------------------------------
    # Frame / instance / image resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_frame(recon, frame: Optional[int]) -> int:
        if frame is not None:
            return int(frame)
        fis = getattr(recon, "frame_indices", None) or list((recon.instances or {}).keys())
        return int(fis[0]) if fis else 0

    @staticmethod
    def _match(inst: dict, target: Union[int, str]) -> bool:
        if isinstance(target, int):
            return False
        t = str(target).strip().lower()
        lab = str(inst.get("label", "")).lower()
        iid = str(inst.get("id", "")).lower()
        return t == lab or t in lab or lab in t or t in iid

    @classmethod
    def _find_target(cls, insts: List[dict], target: Union[int, str]) -> Optional[dict]:
        if isinstance(target, int):
            return insts[target] if 0 <= target < len(insts) else None
        hits = [it for it in insts if cls._match(it, target)]
        return hits[0] if hits else None

    @staticmethod
    def _resolve_label(insts: List[dict], obj: Union[int, str]) -> str:
        if isinstance(obj, str):
            return obj
        if 0 <= obj < len(insts):
            return insts[obj].get("label", f"object_{obj}")
        return f"object_{obj}"

    @staticmethod
    def _frame_image(recon, images, frame: int):
        """Best-effort PIL image for a frame (for base-model judgements)."""

        def _plain(x):
            return x.image if hasattr(x, "image") else x

        fis = list(getattr(recon, "frame_indices", []) or [])
        if images:
            imgs = list(images)
            if frame in fis:
                loc = fis.index(frame)
                if loc < len(imgs):
                    return _plain(imgs[loc])
            return _plain(imgs[0])
        stored = getattr(recon, "_input_images", None)
        if stored:
            if frame in fis and fis.index(frame) < len(stored):
                return stored[fis.index(frame)]
            return stored[0]
        return None

    # ------------------------------------------------------------------
    # Local (viewpoint) frame + target placement
    # ------------------------------------------------------------------

    @classmethod
    def _build_local_frame(cls, vp_inst: dict, cam: Optional[dict]):
        """Build a viewpoint-local 2D frame (origin, forward2d, right2d, origin_y).

        With no mask/pose, the viewpoint object is assumed to face away from the
        camera into the scene (a reasonable default; the base model refines the
        left/right wording later). Forward is the camera->object BEV direction.
        """
        ox, oz = cls._inst_bev_xz(vp_inst, cam)
        origin_y = cls._inst_vertical_m(vp_inst, cam)
        cam_x, cam_z = cam.get("pos_bev", (0.0, 0.0)) if cam else (0.0, 0.0)
        fx, fz = (ox - float(cam_x), oz - float(cam_z))
        n = math.hypot(fx, fz)
        if n < 1e-6:
            fx, fz = 0.0, 1.0
        else:
            fx, fz = fx / n, fz / n
        # right = forward rotated -90 deg in XZ (x=right, z=forward convention)
        rx, rz = (fz, -fx)
        return (ox, oz), (fx, fz), (rx, rz), origin_y

    @classmethod
    def _compute_target_info(
        cls,
        origin_xz,
        forward2d,
        right2d,
        origin_y,
        tgt_inst: dict,
        cam: Optional[dict],
        target_label: str,
        threshold: float,
    ) -> Dict[str, Any]:
        """Place a target in the viewpoint-local frame from instance geometry."""
        tx, tz = cls._inst_bev_xz(tgt_inst, cam)
        ty = cls._inst_vertical_m(tgt_inst, cam)
        rel_x, rel_z = (tx - origin_xz[0], tz - origin_xz[1])
        dz = rel_x * forward2d[0] + rel_z * forward2d[1]  # forward+
        dx = rel_x * right2d[0] + rel_z * right2d[1]  # right+
        dy = ty - origin_y  # up+
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        angle_deg = math.degrees(math.atan2(dx, dz)) if (abs(dx) > 1e-6 or abs(dz) > 1e-6) else 0.0
        return {
            "label": target_label,
            "direction": cls._delta_to_direction(dx, dy, dz, threshold),
            "distance_m": round(dist, 4),
            "angle_deg": round(float(angle_deg), 1),
            "local_xyz": [round(dx, 4), round(dy, 4), round(dz, 4)],
        }

    # ------------------------------------------------------------------
    # Base-model perceptual helpers
    # ------------------------------------------------------------------

    def _vlm_relation(self, img, vp_label: str, tgt_label: str) -> Optional[str]:
        """Ask the base model where target sits from the viewpoint's perspective."""
        if img is None or self._vlm_module is None:
            return None
        q = (
            f"Imagine you are the {vp_label} in this image, looking outward from "
            f"its point of view. Where is the {tgt_label} relative to the "
            f"{vp_label}? Reply with a short phrase using only these words: "
            f"'to the left', 'to the right', 'in front', 'behind', 'above', 'below'."
        )
        try:
            ans = self._vlm_module.ask(img, q)
        except Exception:  # noqa: BLE001
            return None
        if not ans:
            return None
        low = ans.lower()
        parts = []
        if "right" in low:
            parts.append("to the right")
        elif "left" in low:
            parts.append("to the left")
        if "front" in low or "ahead" in low:
            parts.append("in front")
        elif "behind" in low or "back" in low:
            parts.append("behind")
        if "above" in low or "over" in low:
            parts.append("above")
        elif "below" in low or "under" in low:
            parts.append("below")
        return ", ".join(parts) if parts else None

    def _vlm_visible(self, img, vp_label: str, tgt_label: str) -> Optional[bool]:
        """Ask whether the target would be visible from the viewpoint's position."""
        if img is None or self._vlm_module is None:
            return None
        q = (
            f"From the {vp_label}'s point of view in this image, would the "
            f"{tgt_label} be visible (not fully occluded and within its field of "
            f"view)? Answer yes or no."
        )
        try:
            ans = self._vlm_module.ask(img, q)
        except Exception:  # noqa: BLE001
            return None
        if not ans:
            return None
        return "yes" in ans.strip().lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def from_viewpoint(
        self,
        recon,
        frame: Optional[int] = None,
        viewpoint: Union[int, str] = 0,
        targets: Optional[List[Union[int, str]]] = None,
        images=None,
        threshold: float = 0.15,
    ) -> List[Dict[str, Any]]:
        """Describe target objects relative to the viewpoint object.

        Geometry (distance/angle/local_xyz) comes from each instance's bbox center +
        ``depth_m``; the ``direction`` wording is confirmed by the base model on the
        frame image when available, falling back to geometry otherwise.
        """
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        insts = recon.get_instances(frame)
        vp_inst = self._find_target(insts, viewpoint)
        if vp_inst is None:
            return []
        vp_label = vp_inst.get("label", self._resolve_label(insts, viewpoint))
        origin_xz, forward2d, right2d, origin_y = self._build_local_frame(vp_inst, cam)
        img = self._frame_image(recon, images, frame)

        tgt_list = targets if targets is not None else list(range(len(insts)))
        results = []
        for t in tgt_list:
            t_inst = self._find_target(insts, t)
            if t_inst is None or t_inst is vp_inst:
                continue
            t_label = t_inst.get("label", self._resolve_label(insts, t))
            info = self._compute_target_info(origin_xz, forward2d, right2d, origin_y, t_inst, cam, t_label, threshold)
            vlm_dir = self._vlm_relation(img, vp_label, t_label)
            if vlm_dir:
                info["direction"] = vlm_dir
            results.append(info)
        results.sort(key=lambda x: x["distance_m"])
        return results

    def visible_from(
        self,
        recon,
        frame: Optional[int] = None,
        viewpoint: Union[int, str] = 0,
        target: Union[int, str] = 0,
        images=None,
        threshold: float = 0.15,
    ) -> Dict[str, Any]:
        """Check whether a target is visible from the viewpoint, plus its position.

        Returns direction, distance, local coordinates, the viewpoint label, and a
        base-model ``visible`` judgement on the frame image.
        """
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        insts = recon.get_instances(frame)
        vp_inst = self._find_target(insts, viewpoint)
        t_inst = self._find_target(insts, target)
        vp_label = vp_inst.get("label") if vp_inst else self._resolve_label(insts, viewpoint)
        t_label = t_inst.get("label") if t_inst else self._resolve_label(insts, target)
        img = self._frame_image(recon, images, frame)

        if vp_inst is None or t_inst is None:
            return {
                "label": t_label,
                "viewpoint": vp_label,
                "direction": "unknown",
                "distance_m": None,
                "angle_deg": None,
                "local_xyz": None,
                "visible": self._vlm_visible(img, vp_label, t_label),
            }

        origin_xz, forward2d, right2d, origin_y = self._build_local_frame(vp_inst, cam)
        info = self._compute_target_info(origin_xz, forward2d, right2d, origin_y, t_inst, cam, t_label, threshold)
        vlm_dir = self._vlm_relation(img, vp_label, t_label)
        if vlm_dir:
            info["direction"] = vlm_dir
        info["viewpoint"] = vp_label
        info["visible"] = self._vlm_visible(img, vp_label, t_label)
        return info

    def scene_layout(
        self, recon, frame: Optional[int] = None, viewpoint: Union[int, str] = 0, images=None, threshold: float = 0.15
    ) -> Dict[str, Any]:
        """Full spatial layout of all objects from the viewpoint's perspective.

        Returns the viewpoint label, the frame, and a distance-sorted list of the
        other instances with their direction and distance.
        """
        frame = self._resolve_frame(recon, frame)
        insts = recon.get_instances(frame)
        vp_inst = self._find_target(insts, viewpoint)
        vp_label = vp_inst.get("label") if vp_inst else self._resolve_label(insts, viewpoint)
        if vp_inst is None:
            return {"viewpoint": vp_label, "frame": frame, "objects": []}

        objects = self.from_viewpoint(
            recon, frame=frame, viewpoint=viewpoint, targets=None, images=images, threshold=threshold
        )
        return {
            "viewpoint": vp_label,
            "frame": frame,
            "objects": objects,
        }

    # ------------------------------------------------------------------
    # Occlusion / line-of-sight (ADDITIVE)
    # ------------------------------------------------------------------

    @classmethod
    def _ego_xy(cls, inst: dict, cam: Optional[dict]) -> tuple:
        """Ego BEV of an instance: x=right, y=forward (origin=camera).

        bearing = (point_1000[0]/1000 - 0.5) * fov; x = depth*sin(bearing),
        y = depth*cos(bearing).
        """
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        bearing = math.radians(cls._inst_angle_deg(inst, cam))
        return (depth * math.sin(bearing), depth * math.cos(bearing))

    @staticmethod
    def _inst_radius_m(inst: dict, cam: Optional[dict]) -> float:
        """Rough disc radius of an instance from its bbox width (fallback ~0.25m)."""
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        bb = inst.get("bbox_1000")
        if bb and depth > 0:
            width_frac = max(0.0, (float(bb[2]) - float(bb[0])) / 1000.0)
            fov = ViewExpert._fov_deg(cam)
            half_w = depth * math.tan(math.radians(fov * width_frac / 2.0))
            if half_w > 1e-3:
                return float(half_w)
        return 0.25

    def line_of_sight(
        self,
        recon,
        frame: Optional[int] = None,
        viewpoint: Union[int, str] = 0,
        target: Union[int, str] = 0,
        images=None,
    ) -> Dict[str, Any]:
        """Occlusion / visibility between two objects on the BEV.

        Resolves the viewpoint and target instances, computes each object's ego BEV
        (x=right, y=forward), and treats the segment viewpoint->target as the line of
        sight. Every OTHER instance is a small disc (radius from its bbox width, or
        ~0.25m); a blocker is one whose disc the segment passes within AND whose
        projection along the segment falls between the endpoints. When images are
        available, a base-model visibility check is added under ``vlm_visible``.
        """
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        insts = recon.get_instances(frame) or []
        vp_inst = self._find_target(insts, viewpoint)
        tgt_inst = self._find_target(insts, target)
        vp_label = vp_inst.get("label") if vp_inst else self._resolve_label(insts, viewpoint)
        tgt_label = tgt_inst.get("label") if tgt_inst else self._resolve_label(insts, target)

        if vp_inst is None or tgt_inst is None:
            return {
                "viewpoint": vp_label,
                "target": tgt_label,
                "clear": None,
                "blockers": [],
                "note": "viewpoint or target not found among instances",
            }

        vx, vy = self._ego_xy(vp_inst, cam)
        tx, ty = self._ego_xy(tgt_inst, cam)
        seg_dx, seg_dy = (tx - vx), (ty - vy)
        seg_len2 = seg_dx * seg_dx + seg_dy * seg_dy

        blockers = []
        for it in insts:
            if it is vp_inst or it is tgt_inst:
                continue
            ox, oy = self._ego_xy(it, cam)
            radius = self._inst_radius_m(it, cam)
            if seg_len2 < 1e-9:
                continue
            t = ((ox - vx) * seg_dx + (oy - vy) * seg_dy) / seg_len2
            if t <= 0.0 or t >= 1.0:
                continue
            px, py = (vx + t * seg_dx, vy + t * seg_dy)
            dist_to_line = math.hypot(ox - px, oy - py)
            if dist_to_line <= radius:
                blockers.append(
                    {
                        "label": it.get("label", "object"),
                        "dist_to_line_m": round(dist_to_line, 3),
                    }
                )

        blockers.sort(key=lambda b: b["dist_to_line_m"])
        out: Dict[str, Any] = {
            "viewpoint": vp_label,
            "target": tgt_label,
            "clear": len(blockers) == 0,
            "blockers": blockers,
            "note": ("clear line of sight" if not blockers else f"{len(blockers)} potential blocker(s)"),
        }

        img = self._frame_image(recon, images, frame)
        if img is not None and self._vlm_module is not None:
            try:
                ans = self._vlm_module.ask(
                    img, f"Is the {tgt_label} visible / not occluded from near the {vp_label}? yes/no"
                )
                if ans:
                    out["vlm_visible"] = "yes" in ans.strip().lower()
            except Exception:  # noqa: BLE001
                pass
        return out

    def __repr__(self) -> str:
        return "ViewExpert(methods: from_viewpoint, visible_from, scene_layout)"
