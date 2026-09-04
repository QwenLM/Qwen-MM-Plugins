"""Mobile manipulation expert (model-free).

Reachability, movement/navigation planning and BEV visualization computed ENTIRELY
from the instance-level, model-free reconstruction:

- ``recon.instances[t]`` — per-frame ``{id, label, bbox_1000, point_1000, depth_m, ...}``.
- ``recon.cameras[t]``   — per-frame ``{pos_bev, yaw_deg, fov_deg, intrinsics, ...}``.
- ``recon.bev_visual(...)`` — top-down BEV.
- ``vlm.ask`` / ``vlm.locate`` — base-model fallbacks when an object is not among
  the reconstructed instances.

No dense point cloud, segmentation mask, or camera-extrinsics matrix is used. Public
method names and returned dict field names are preserved so callers are unchanged.
The VLM handle is injected via ``set_vlm_module`` (mirrors Reconstruct), so methods
do not take a ``vlm`` argument.
"""

import math
from typing import Any, Dict, List, Optional, Union

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class MobileManipulationExpert(CPUTool):
    """Reachability + mobile-base planning over the model-free instance scene."""

    _gt_obstacles = None  # legacy hook still set by workflow.py; unused here.

    TOOL_PROMPT_DESCRIPTION = ""  # excluded from the agent tool directory.

    DEFAULT_MAX_REACH_M = 1.0
    DEFAULT_LATERAL_SPREAD = 0.3

    def __init__(self):
        self._vlm_module = None
        self._tracer = None

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    # Instance-level helpers (spec: _inst_lateral_offset / _inst_angle_deg / _inst_bev_xz)
    # ------------------------------------------------------------------

    @staticmethod
    def _fov_deg(cam: Optional[dict]) -> float:
        if cam and cam.get("fov_deg"):
            return float(cam["fov_deg"])
        return 60.0

    @staticmethod
    def _inst_angle_deg(inst: dict, cam: Optional[dict]) -> float:
        """Horizontal bearing of an instance from camera forward, in degrees (+right).

        Uses the bbox center x (``point_1000[0]`` in 0..1000) and the camera FOV.
        """
        px = float(inst.get("point_1000", [500.0, 500.0])[0])
        frac = px / 1000.0 - 0.5  # -0.5 .. +0.5
        return frac * MobileManipulationExpert._fov_deg(cam)

    @staticmethod
    def _inst_lateral_offset(inst: dict, cam: Optional[dict]) -> float:
        """Lateral (right+) offset of an instance in meters: depth * tan(bearing)."""
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        ang = math.radians(MobileManipulationExpert._inst_angle_deg(inst, cam))
        return depth * math.tan(ang)

    @staticmethod
    def _inst_bev_xz(inst: dict, cam: Optional[dict]) -> tuple:
        """World BEV (x, z) meters of an instance, using the camera pose.

        Ego frame: z = forward = depth*cos(bearing), x = right = depth*sin(bearing).
        Rotated by camera yaw and translated by camera ``pos_bev`` into world XZ.
        """
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        ang = math.radians(MobileManipulationExpert._inst_angle_deg(inst, cam))
        ego_x = depth * math.sin(ang)  # right
        ego_z = depth * math.cos(ang)  # forward
        if not cam:
            return (ego_x, ego_z)
        yaw = math.radians(float(cam.get("yaw_deg", 0.0)))
        px, pz = cam.get("pos_bev", (0.0, 0.0))
        # forward_world = (sin yaw, -cos yaw); right_world = (cos yaw, sin yaw)
        world_x = float(px) + ego_z * math.sin(yaw) + ego_x * math.cos(yaw)
        world_z = float(pz) - ego_z * math.cos(yaw) + ego_x * math.sin(yaw)
        return (world_x, world_z)

    @staticmethod
    def _dir8(angle_deg: float) -> str:
        """Map a bearing (deg, +right) to an 8-way label."""
        a = ((angle_deg + 180.0) % 360.0) - 180.0
        aa = abs(a)
        if aa <= 22.5:
            return "forward"
        if aa >= 157.5:
            return "backward"
        side = "right" if a > 0 else "left"
        if aa <= 67.5:
            return f"forward-{side}"
        if aa <= 112.5:
            return side
        return f"backward-{side}"

    # ------------------------------------------------------------------
    # Frame / instance resolution
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
    def _frame_image(recon, images, frame: int):
        """Best-effort PIL image for a frame (for VLM fallbacks)."""

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

    def _resolve_objects(self, insts: List[dict], objects: Optional[List[Union[int, str]]]) -> List[dict]:
        if objects is None:
            return list(insts)
        out = []
        for o in objects:
            it = self._find_target(insts, o)
            if it is not None:
                out.append(it)
        return out

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def reachability(
        self,
        recon,
        frame: Optional[int] = None,
        objects: Optional[List[Union[int, str]]] = None,
        max_reach_m: float = DEFAULT_MAX_REACH_M,
    ) -> Dict[str, Any]:
        """Which objects are within arm reach vs. need mobile().

        distance = instance ``depth_m``; reachable = distance <= ``max_reach_m``;
        direction from the instance's horizontal bearing (8-way label).
        """
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        insts = self._resolve_objects(recon.get_instances(frame), objects)

        entries = []
        for it in insts:
            dist = float(it.get("depth_m", 0.0) or 0.0)
            direction = self._dir8(self._inst_angle_deg(it, cam))
            entries.append(
                {
                    "label": it.get("label", "object"),
                    "id": it.get("id"),
                    "distance_m": round(dist, 3),
                    "direction": direction,
                    "reachable": dist <= max_reach_m,
                }
            )
        entries.sort(key=lambda x: x["distance_m"])
        return {
            "frame": frame,
            "reach_threshold_m": round(float(max_reach_m), 3),
            "objects": entries,
        }

    # ------------------------------------------------------------------
    # Movement / navigation planning
    # ------------------------------------------------------------------

    def plan_navigation(
        self,
        recon,
        frame: Optional[int] = None,
        target: Union[int, str] = 0,
    ) -> Dict[str, Any]:
        """Plan a turn + go_forward route to a target from its bearing + depth."""
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        insts = recon.get_instances(frame)
        it = self._find_target(insts, target)
        label = it.get("label") if it else (target if isinstance(target, str) else f"object_{target}")
        if it is None:
            return {
                "target": label,
                "found": False,
                "angle_deg": None,
                "distance_m": None,
                "turn_action": None,
                "direction": None,
                "skill_sequence": [f"observation({label})", f"go_forward({label})"],
            }

        angle_deg = self._inst_angle_deg(it, cam)
        dist = float(it.get("depth_m", 0.0) or 0.0)

        skills = []
        if abs(angle_deg) > 135:
            skills.append("turn_back()")
            turn_desc = "turn_back (180°)"
        elif angle_deg > 45:
            skills.append("turn_right()")
            turn_desc = "turn_right (90°)"
        elif angle_deg < -45:
            skills.append("turn_left()")
            turn_desc = "turn_left (90°)"
        else:
            turn_desc = "none (already facing)"
        skills.append(f"go_forward({label})")

        return {
            "target": label,
            "found": True,
            "angle_deg": round(float(angle_deg), 1),
            "distance_m": round(dist, 3),
            "turn_action": turn_desc,
            "direction": self._dir8(angle_deg),
            "skill_sequence": skills,
        }

    def plan_movement(
        self,
        recon,
        frame: Optional[int] = None,
        target: Union[int, str] = 0,
        max_reach_m: float = DEFAULT_MAX_REACH_M,
    ) -> Dict[str, Any]:
        """Plan the base movement to approach a target.

        If the target is already reachable, no movement is required; otherwise the
        route is the ``plan_navigation`` turn + go_forward sequence.
        """
        frame = self._resolve_frame(recon, frame)
        insts = recon.get_instances(frame)
        it = self._find_target(insts, target)
        label = it.get("label") if it else (target if isinstance(target, str) else f"object_{target}")

        if it is not None and float(it.get("depth_m", 0.0) or 0.0) <= max_reach_m:
            return {
                "target": label,
                "reachable": True,
                "total_distance_m": round(float(it.get("depth_m", 0.0) or 0.0), 3),
                "num_steps": 0,
                "steps": [],
                "skill_sequence": [],
            }

        nav = self.plan_navigation(recon, frame=frame, target=target)
        steps = [{"step": i + 1, "skill": s} for i, s in enumerate(nav["skill_sequence"])]
        return {
            "target": label,
            "reachable": False,
            "total_distance_m": nav.get("distance_m"),
            "num_steps": len(steps),
            "steps": steps,
            "skill_sequence": nav["skill_sequence"],
        }

    def suggest_approach(
        self,
        recon,
        frame: Optional[int] = None,
        target: Union[int, str] = 0,
        obstacles: Optional[List[Union[int, str]]] = None,
    ) -> Dict[str, Any]:
        """Pre-contact staging: score 4 approach directions around the target.

        Staging positions are placed 0.5m out from the target along front/back/
        left/right; each is scored by clearance to other instances (BEV) minus
        distance from the robot.
        """
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        insts = recon.get_instances(frame)
        tgt = self._find_target(insts, target)
        target_label = tgt.get("label") if tgt else (target if isinstance(target, str) else f"object_{target}")
        if tgt is None:
            return {"target": target_label, "found": False, "skill_sequence": [f"observation({target_label})"]}

        tx, tz = self._inst_bev_xz(tgt, cam)
        robot = np.asarray(cam.get("pos_bev", (0.0, 0.0)) if cam else (0.0, 0.0), dtype=float)

        if obstacles is None:
            obs_insts = [it for it in insts if it is not tgt]
        else:
            obs_insts = [x for x in (self._find_target(insts, o) for o in obstacles) if x]
        obs_xz = [np.asarray(self._inst_bev_xz(it, cam), dtype=float) for it in obs_insts]

        staging = 0.5
        target_xz = np.array([tx, tz])
        directions = {"front": (0.0, 1.0), "back": (0.0, -1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
        candidates = []
        for name, (dx, dz) in directions.items():
            pos = target_xz - np.array([dx, dz]) * staging
            clearance = min((float(np.linalg.norm(pos - o)) for o in obs_xz), default=float("inf"))
            dist_robot = float(np.linalg.norm(pos - robot))
            score = (clearance * 2.0 if clearance != float("inf") else 10.0) - dist_robot * 0.5
            candidates.append(
                {
                    "direction": name,
                    "staging_position": [round(float(pos[0]), 3), round(float(pos[1]), 3)],
                    "clearance_m": (round(clearance, 3) if clearance != float("inf") else None),
                    "distance_from_robot_m": round(dist_robot, 3),
                    "score": round(score, 3),
                }
            )
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]

        skills = []
        if best["distance_from_robot_m"] > max(self.DEFAULT_MAX_REACH_M, 0.8):
            skills.append(f"mobile({target_label})")
        skills.append(f"move_to(none, {target_label})")
        return {
            "target": target_label,
            "found": True,
            "best_direction": best["direction"],
            "staging_position": best["staging_position"],
            "clearance_m": best["clearance_m"],
            "distance_from_robot_m": best["distance_from_robot_m"],
            "all_candidates": candidates,
            "skill_sequence": skills,
        }

    def detect_bimanual_layout(
        self,
        recon,
        frame: Optional[int] = None,
        objects: Optional[List[Union[int, str]]] = None,
    ) -> Dict[str, Any]:
        """Assign objects to left/right arm from their bbox center vs image midline.

        bbox center x < 500 → left, else right. Lateral spread > 0.3 (fraction of
        width) → dual-arm task, else single-arm.
        """
        frame = self._resolve_frame(recon, frame)
        insts = self._resolve_objects(recon.get_instances(frame), objects)

        entries = []
        for it in insts:
            cx = float(it.get("point_1000", [500.0, 500.0])[0]) / 1000.0  # 0..1
            entries.append(
                {
                    "label": it.get("label", "object"),
                    "center_x": round(cx, 3),
                    "side": "left" if cx < 0.5 else "right",
                }
            )
        left_arm = [e["label"] for e in entries if e["side"] == "left"]
        right_arm = [e["label"] for e in entries if e["side"] == "right"]

        if len(entries) >= 2:
            spread = max(e["center_x"] for e in entries) - min(e["center_x"] for e in entries)
            embodiment = "dual_arm" if spread > self.DEFAULT_LATERAL_SPREAD else "single_arm"
        else:
            embodiment = "single_arm"

        result: Dict[str, Any] = {
            "left_arm": left_arm,
            "right_arm": right_arm,
            "embodiment": embodiment,
            "objects": entries,
        }
        if embodiment == "dual_arm":
            result["format_hint"] = (
                "Use 'left:<skill>, right:<skill>' format. When one arm is idle, use "
                f"'no_ops'. Left arm objects: {left_arm}. Right arm objects: {right_arm}."
            )
        else:
            result["format_hint"] = "Single-arm task. Use standard skill format without left:/right: prefix."
        return result

    # ------------------------------------------------------------------
    # Visibility & search
    # ------------------------------------------------------------------

    def check_object_in_view(
        self,
        recon,
        images,
        frame: Optional[int] = None,
        target: Union[int, str] = 0,
    ) -> Dict[str, Any]:
        """Is a target visible in the frame?

        True if the target is among ``recon.get_instances(frame)`` (name/label
        match); otherwise a ``vlm.ask`` yes/no fallback on the frame image.
        """
        frame = self._resolve_frame(recon, frame)
        label = target if isinstance(target, str) else f"object_{target}"
        it = self._find_target(recon.get_instances(frame), target)
        if it is not None:
            return {
                "label": it.get("label", label),
                "in_view": True,
                "source": "instances",
                "bbox_1000": it.get("bbox_1000"),
            }

        img = self._frame_image(recon, images, frame)
        if img is not None and self._vlm_module is not None:
            try:
                ans = self._vlm_module.ask(img, f"Is a {label} visible in this image? Answer yes or no.")
                in_view = "yes" in (ans or "").strip().lower()
            except Exception:  # noqa: BLE001
                in_view = False
        else:
            in_view = False
        result = {"label": label, "in_view": in_view, "source": "vlm"}
        if not in_view:
            result["skill_needed"] = f"observation({label})"
        return result

    def search_object_across_frames(
        self,
        recon,
        images,
        target: Union[int, str],
    ) -> Dict[str, Any]:
        """Search all frames for a target.

        First scans ``recon.instances[t]`` per frame; on a hit returns the first
        frame + bbox. If no instance matches, falls back to ``vlm.locate`` per
        frame (a non-"Not visible" answer counts as a hit).
        """
        label = target if isinstance(target, str) else f"object_{target}"
        fis = list(getattr(recon, "frame_indices", []) or sorted((recon.instances or {}).keys()))

        hits = []
        for fi in fis:
            it = self._find_target(recon.get_instances(fi), target)
            if it is not None:
                hits.append({"frame": fi, "bbox_1000": it.get("bbox_1000"), "depth_m": it.get("depth_m")})
        if hits:
            return {
                "label": label,
                "found": True,
                "source": "instances",
                "first_seen_frame": hits[0]["frame"],
                "best_frame": hits[0]["frame"],
                "all_hits": hits,
            }

        # VLM fallback: probe each frame with vlm.locate.
        if self._vlm_module is not None:
            for fi in fis:
                img = self._frame_image(recon, images, fi)
                if img is None:
                    continue
                try:
                    ans = self._vlm_module.locate(
                        img,
                        f"Give the center of the {label} as (x=.., y=..) in 0-1000 normalized scale, or 'Not visible'.",
                    )
                except Exception:  # noqa: BLE001
                    continue
                if ans and "not visible" not in ans.strip().lower():
                    return {
                        "label": label,
                        "found": True,
                        "source": "vlm.locate",
                        "first_seen_frame": fi,
                        "best_frame": fi,
                        "all_hits": [{"frame": fi, "answer": ans.strip()[:80]}],
                    }

        return {
            "label": label,
            "found": False,
            "source": "none",
            "first_seen_frame": None,
            "best_frame": None,
            "all_hits": [],
            "recommendation": (
                f"'{label}' not found in any frame. Use plan_active_search() to plan a "
                f"physical search, or emit observation({label}) in a closed-loop setting."
            ),
        }

    def plan_active_search(
        self,
        recon,
        frames,
        target: Union[int, str],
        max_moves: int = 3,
    ) -> Dict[str, Any]:
        """Plan a closed-loop search for an unseen target from the camera track.

        Uses the camera BEV trajectory (from ``recon.cameras``) to suggest the next
        move (turn toward an unobserved side / continue forward). Text-level plan.
        """
        label = target if isinstance(target, str) else f"object_{target}"
        cams = recon.cameras or {}
        fis = sorted(cams.keys())

        traj = [cams[fi].get("pos_bev", (0.0, 0.0)) for fi in fis]
        turned_left = any(cams[fi].get("yaw_delta_deg", 0.0) < -10 for fi in fis)
        turned_right = any(cams[fi].get("yaw_delta_deg", 0.0) > 10 for fi in fis)

        steps = [f"observation({label})"]
        plan = []
        if not turned_right:
            plan.append({"action": "turn_right", "reason": "right side not yet observed"})
            steps.append("turn_right()")
        elif not turned_left:
            plan.append({"action": "turn_left", "reason": "left side not yet observed"})
            steps.append("turn_left()")
        else:
            plan.append({"action": "go_forward", "reason": "both sides scanned; advance to a new vantage point"})
            steps.append(f"go_forward({label})")
        steps.append(f"look_for({label})")

        return {
            "target": label,
            "found_in_existing": False,
            "camera_trajectory_bev": [[round(float(x), 3), round(float(z), 3)] for x, z in traj],
            "turned_left": turned_left,
            "turned_right": turned_right,
            "search_plan": plan[:max_moves],
            "skill_sequence": steps,
        }

    # ------------------------------------------------------------------
    # Trajectory
    # ------------------------------------------------------------------

    def track_object_trajectory(self, recon, target: Union[int, str]) -> Dict[str, Any]:
        """Cross-frame BEV trajectory of a target.

        If a canonical instance id matches across frames, uses the instance track;
        otherwise picks the best label match per frame. Positions are BEV (x, z)
        via ``_inst_bev_xz``.
        """
        label = target if isinstance(target, str) else f"object_{target}"
        fis = list(getattr(recon, "frame_indices", []) or sorted((recon.instances or {}).keys()))

        trajectory = []
        for fi in fis:
            cam = recon.get_camera(fi)
            it = self._find_target(recon.get_instances(fi), target)
            if it is None:
                continue
            x, z = self._inst_bev_xz(it, cam)
            trajectory.append(
                {
                    "frame": int(fi),
                    "position_bev": [round(float(x), 4), round(float(z), 4)],
                    "depth_m": round(float(it.get("depth_m", 0.0) or 0.0), 4),
                }
            )

        if len(trajectory) < 2:
            return {
                "label": label,
                "num_frames": len(trajectory),
                "trajectory": trajectory,
                "displacement_m": 0.0,
                "is_moving": False,
                "note": "Too few frames with this target for trajectory analysis",
            }

        pts = np.array([t["position_bev"] for t in trajectory])
        steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        total = float(np.sum(steps))
        extent = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
        return {
            "label": label,
            "num_frames": len(trajectory),
            "trajectory": trajectory,
            "displacement_m": round(total, 4),
            "bbox_extent_m": round(extent, 4),
            "is_moving": extent > 0.1,
        }

    # ------------------------------------------------------------------
    # BEV visualization (thin wrappers over recon.bev_visual)
    # ------------------------------------------------------------------

    def render_reachability_bev(
        self,
        recon,
        frame: Optional[int] = None,
        objects: Optional[List[Union[int, str]]] = None,
        max_reach_m: float = DEFAULT_MAX_REACH_M,
    ):
        """Instance-level BEV with a reachability digest.

        Renders ``recon.bev_visual`` (top-down instances + camera) and annotates the
        returned feedback with which objects are reachable vs. need mobile().
        """
        frame = self._resolve_frame(recon, frame)
        reach = self.reachability(recon, frame=frame, objects=objects, max_reach_m=max_reach_m)
        ids = None
        if objects is not None:
            ids = [it.get("id") for it in self._resolve_objects(recon.get_instances(frame), objects)]
        vf = recon.bev_visual(ref_frame=frame, show_instances=True, ids=ids)

        reachable = [e["label"] for e in reach["objects"] if e["reachable"]]
        need = [e["label"] for e in reach["objects"] if not e["reachable"]]
        extra = f" Reachable: {', '.join(reachable) or '—'}. Need mobile(): {', '.join(need) or '—'}."
        try:
            vf.description = (getattr(vf, "description", "") or "") + extra
        except Exception:  # noqa: BLE001
            pass
        return vf

    def estimate_bev_from_prompt(self, recon, frame: Optional[int] = None):
        """Unified top-down room BEV for the clip (thin wrapper over bev_visual)."""
        frame = self._resolve_frame(recon, frame)
        return recon.bev_visual(ref_frame=frame, show_instances=True, show_trajectory=True)

    def render_trajectory_on_bev(self, recon, frame: Optional[int] = None, ids: Optional[List[str]] = None):
        """BEV with the camera trajectory drawn (bev_visual show_trajectory=True)."""
        frame = self._resolve_frame(recon, frame)
        return recon.bev_visual(ref_frame=frame, show_trajectory=True, show_instances=True, ids=ids)

    # ------------------------------------------------------------------
    # Free-space BEV + path planning (ego frame: x=right, y=forward, origin=camera)
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_obstacles(obstacles) -> List[dict]:
        """Accept a free_space_bev dict, its 'obstacles' list, or a list of dicts."""
        if obstacles is None:
            return []
        if isinstance(obstacles, dict):
            obstacles = obstacles.get("obstacles", [])
        out = []
        for o in obstacles:
            if isinstance(o, dict) and o.get("polygon_ego"):
                out.append(o)
        return out

    @staticmethod
    def _inflate_polygon(poly: np.ndarray, clearance: float) -> np.ndarray:
        """Expand a polygon outward from its centroid by ``clearance`` meters."""
        c = poly.mean(axis=0)
        out = np.empty_like(poly)
        for i, p in enumerate(poly):
            d = p - c
            n = float(np.linalg.norm(d))
            if n < 1e-9:
                out[i] = p
            else:
                out[i] = p + d / n * clearance
        return out

    @staticmethod
    def _point_in_polygon(pt, poly: np.ndarray) -> bool:
        """Ray-casting point-in-polygon test (poly = (N,2) array)."""
        x, y = float(pt[0]), float(pt[1])
        n = len(poly)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi):
                inside = not inside
            j = i
        return inside

    @staticmethod
    def _point_to_polygon_dist(pt, poly: np.ndarray) -> float:
        """Minimum distance from a point to the edges of a closed polygon."""
        x, y = float(pt[0]), float(pt[1])
        best = float("inf")
        n = len(poly)
        for i in range(n):
            ax, ay = poly[i]
            bx, by = poly[(i + 1) % n]
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            if seg2 < 1e-12:
                d = math.hypot(x - ax, y - ay)
            else:
                t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg2))
                px, py = ax + t * dx, ay + t * dy
                d = math.hypot(x - px, y - py)
            best = min(best, d)
        return best

    def _render_bev(
        self,
        obstacles,
        goal_ego=None,
        start_ego=(0.0, 0.0),
        waypoints=None,
        min_clearance=None,
        source="Mobile BEV",
        description="",
    ):
        """Render an ego-frame BEV (x=right, y=forward) to a VisualFeedback."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPolygon
        from PIL import Image

        from qwen_mm_plugins_video_spatio.visual_feedback import VisualFeedback

        fig, ax = plt.subplots(1, 1, figsize=(6, 6))
        xs, ys = [float(start_ego[0])], [float(start_ego[1])]
        for ob in obstacles:
            poly = np.asarray(ob["polygon_ego"], dtype=float)
            ax.add_patch(MplPolygon(poly, closed=True, facecolor="gray", edgecolor="dimgray", alpha=0.5))
            c = poly.mean(axis=0)
            ax.text(c[0], c[1], str(ob.get("label", "")), fontsize=7, ha="center", va="center", color="black")
            xs.extend(poly[:, 0].tolist())
            ys.extend(poly[:, 1].tolist())

        # ego robot: blue triangle at (0,0) facing +y (forward)
        sx, sy = float(start_ego[0]), float(start_ego[1])
        tri = np.array([[sx, sy + 0.35], [sx - 0.25, sy - 0.2], [sx + 0.25, sy - 0.2]])
        ax.add_patch(MplPolygon(tri, closed=True, facecolor="blue", edgecolor="navy"))

        if goal_ego is not None:
            gx, gy = float(goal_ego[0]), float(goal_ego[1])
            ax.plot([gx], [gy], marker="*", markersize=18, color="red")
            xs.append(gx)
            ys.append(gy)

        if waypoints:
            wp = np.asarray(waypoints, dtype=float)
            ax.plot(wp[:, 0], wp[:, 1], "-o", color="green", markersize=4, linewidth=2)
            xs.extend(wp[:, 0].tolist())
            ys.extend(wp[:, 1].tolist())

        if min_clearance is not None:
            ax.set_title(f"min clearance = {min_clearance:.2f} m")

        pad = 0.5
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("x = right (m)")
        ax.set_ylabel("y = forward (m)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        fig.canvas.draw()
        buf = fig.canvas.buffer_rgba()
        img = Image.frombuffer("RGBA", fig.canvas.get_width_height(), buf).convert("RGB")
        plt.close(fig)
        return VisualFeedback(image=img, source=source, description=description)

    def free_space_bev(
        self,
        recon,
        frame: Optional[int] = None,
        objects: Optional[List[Union[int, str]]] = None,
        goal_ego=None,
        obstacle_depth_m: float = 0.4,
    ) -> Dict[str, Any]:
        """Ego-frame free-space / obstacle map from the reconstructed instances.

        Each instance becomes an axis-aligned rectangular footprint centered at its
        ego position (x=right, y=forward, origin=camera). Returns the obstacle list,
        a text digest, and a rendered BEV (VisualFeedback).
        """
        frame = self._resolve_frame(recon, frame)
        cam = recon.get_camera(frame)
        fov = self._fov_deg(cam)
        insts = self._resolve_objects(recon.get_instances(frame), objects)

        obstacles = []
        lines = []
        for it in insts:
            depth = float(it.get("depth_m", 0.0) or 0.0)
            bearing = math.radians(self._inst_angle_deg(it, cam))
            ego_x = depth * math.sin(bearing)  # right
            ego_y = depth * math.cos(bearing)  # forward
            bbox = it.get("bbox_1000", [0.0, 0.0, 0.0, 0.0])
            bbox_width_frac = max(0.0, (float(bbox[2]) - float(bbox[0])) / 1000.0)
            width_x = max(0.2, depth * math.tan(math.radians(fov * bbox_width_frac / 2.0)) * 2.0)
            depth_y = float(obstacle_depth_m)
            hx, hy = width_x / 2.0, depth_y / 2.0
            polygon = [
                [ego_x - hx, ego_y - hy],
                [ego_x + hx, ego_y - hy],
                [ego_x + hx, ego_y + hy],
                [ego_x - hx, ego_y + hy],
            ]
            label = it.get("label", "object")
            obstacles.append(
                {
                    "label": label,
                    "centroid_ego": [round(ego_x, 3), round(ego_y, 3)],
                    "polygon_ego": [[round(px, 3), round(py, 3)] for px, py in polygon],
                }
            )
            lines.append(f"{label}: ({ego_x:.2f}, {ego_y:.2f})")

        obstacle_text = "\n".join(lines)
        bev = self._render_bev(
            obstacles,
            goal_ego=goal_ego,
            source="Mobile free-space BEV",
            description=f"{len(obstacles)} obstacle(s) in ego frame (x=right, y=forward).\n" + obstacle_text,
        )
        return {
            "mode": "instances",
            "frame": frame,
            "num_obstacles": len(obstacles),
            "obstacles": obstacles,
            "obstacle_text": obstacle_text,
            "bev_image": bev,
        }

    def plan_path(
        self,
        goal_ego,
        obstacles,
        start_ego=(0.0, 0.0),
        clearance: float = 0.4,
        grid_res: float = 0.15,
        max_cells: int = 200,
    ) -> Dict[str, Any]:
        """8-connected A* over a coarse ego-frame occupancy grid.

        ``obstacles`` may be a free_space_bev dict, its ``obstacles`` list, or a list
        of ``{"polygon_ego": [[x, y], ...]}``. Returns start-first waypoints (meters).
        """
        import heapq

        obs_list = self._coerce_obstacles(obstacles)
        polys = [self._inflate_polygon(np.asarray(o["polygon_ego"], dtype=float), clearance) for o in obs_list]

        start = (float(start_ego[0]), float(start_ego[1]))
        goal = (float(goal_ego[0]), float(goal_ego[1]))

        xs = [start[0], goal[0]]
        ys = [start[1], goal[1]]
        for p in polys:
            xs.extend(p[:, 0].tolist())
            ys.extend(p[:, 1].tolist())
        min_x, max_x = min(xs) - 1.0, max(xs) + 1.0
        min_y, max_y = min(ys) - 1.0, max(ys) + 1.0
        span_x, span_y = max_x - min_x, max_y - min_y

        res = max(grid_res, span_x / max_cells, span_y / max_cells)
        nx = max(1, int(math.ceil(span_x / res)) + 1)
        ny = max(1, int(math.ceil(span_y / res)) + 1)

        def node_xy(i, j):
            return (min_x + i * res, min_y + j * res)

        def blocked(i, j):
            pt = node_xy(i, j)
            for poly in polys:
                if self._point_in_polygon(pt, poly):
                    return True
            return False

        def to_cell(pt):
            i = int(round((pt[0] - min_x) / res))
            j = int(round((pt[1] - min_y) / res))
            return (min(max(i, 0), nx - 1), min(max(j, 0), ny - 1))

        start_c = to_cell(start)
        goal_c = to_cell(goal)

        def heur(c):
            ax, ay = node_xy(*c)
            gx, gy = node_xy(*goal_c)
            return math.hypot(ax - gx, ay - gy)

        neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        open_heap = [(heur(start_c), 0.0, start_c)]
        g_score = {start_c: 0.0}
        came_from = {}
        found = False
        while open_heap:
            _, g, cur = heapq.heappop(open_heap)
            if cur == goal_c:
                found = True
                break
            if g > g_score.get(cur, float("inf")):
                continue
            ci, cj = cur
            for di, dj in neighbors:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < nx and 0 <= nj < ny):
                    continue
                if blocked(ni, nj):
                    continue
                step = math.hypot(di, dj) * res
                ng = g + step
                nb = (ni, nj)
                if ng < g_score.get(nb, float("inf")):
                    g_score[nb] = ng
                    came_from[nb] = cur
                    heapq.heappush(open_heap, (ng + heur(nb), ng, nb))

        if not found:
            return {
                "waypoints": [[round(start[0], 3), round(start[1], 3)], [round(goal[0], 3), round(goal[1], 3)]],
                "collision_free": False,
                "num_waypoints": 2,
            }

        # Reconstruct + simplify (drop collinear intermediate points).
        cells = [goal_c]
        cur = goal_c
        while cur != start_c:
            cur = came_from[cur]
            cells.append(cur)
        cells.reverse()
        pts = [list(node_xy(*c)) for c in cells]
        pts[0] = [start[0], start[1]]
        pts[-1] = [goal[0], goal[1]]

        simplified = [pts[0]]
        for k in range(1, len(pts) - 1):
            ax_, ay_ = simplified[-1]
            bx_, by_ = pts[k]
            cx_, cy_ = pts[k + 1]
            v1 = (bx_ - ax_, by_ - ay_)
            v2 = (cx_ - bx_, cy_ - by_)
            cross = v1[0] * v2[1] - v1[1] * v2[0]
            if abs(cross) > 1e-6:
                simplified.append(pts[k])
        simplified.append(pts[-1])
        waypoints = [[round(x, 3), round(y, 3)] for x, y in simplified]
        return {
            "waypoints": waypoints,
            "collision_free": True,
            "num_waypoints": len(waypoints),
        }

    def render_path_on_bev(self, obstacles, waypoints, goal_ego=None, start_ego=(0.0, 0.0)):
        """Render obstacles + planned path in the ego frame, annotating min clearance."""
        obs_list = self._coerce_obstacles(obstacles)
        polys = [np.asarray(o["polygon_ego"], dtype=float) for o in obs_list]

        # Min clearance: min distance from any sampled path point to any polygon edge.
        min_clear = float("inf")
        wp = [list(map(float, w)) for w in (waypoints or [])]
        for k in range(len(wp)):
            samples = [wp[k]]
            if k + 1 < len(wp):
                a = np.asarray(wp[k])
                b = np.asarray(wp[k + 1])
                seg_len = float(np.linalg.norm(b - a))
                n = max(1, int(seg_len / 0.1))
                samples = [(a + (b - a) * (s / n)).tolist() for s in range(n + 1)]
            for pt in samples:
                for poly in polys:
                    min_clear = min(min_clear, self._point_to_polygon_dist(pt, poly))
        if min_clear == float("inf"):
            min_clear = None

        return self._render_bev(
            obs_list,
            goal_ego=goal_ego,
            start_ego=start_ego,
            waypoints=wp,
            min_clearance=min_clear,
            source="Mobile path BEV",
            description=f"Planned path with {len(wp)} waypoint(s) in ego frame (x=right, y=forward).",
        )

    def __repr__(self) -> str:
        return (
            "MobileManipulationExpert(methods: reachability, plan_movement, "
            "plan_navigation, suggest_approach, detect_bimanual_layout, "
            "check_object_in_view, search_object_across_frames, plan_active_search, "
            "track_object_trajectory, render_reachability_bev, estimate_bev_from_prompt, "
            "render_trajectory_on_bev)"
        )
