"""ReconstructPromptTool — model-free instance producer (no external perception).

This is the ONLY reconstruction path: there is no 3D-reconstruction model and no
segmentation model. ``tools.Reconstruct.Reconstruct(frames)`` asks the agent's OWN
base model to list salient objects with a bounding box and an estimated metric
depth (``_estimate_objects``), then emits an **instance-level** ``Reconstruction``:

- ``recon.instances[t]`` — per-frame list of ``{id, label, bbox_1000, bbox_pixel,
  point_1000, depth_m, conf, frame_idx}``.
- ``recon.cameras[t]``   — per-frame pose ``{pos_bev, move_vec_bev, yaw_deg,
  yaw_delta_deg, pitch_deg, fov_deg, intrinsics}``.

No dense point cloud is produced. Downstream reasoning uses ``recon.instances`` /
``recon.cameras`` / ``recon.bev_visual(...)`` / ``recon.summary(t)``.

World convention: +Y up, first camera looks -Z, camera at origin. With
``enable_prompt_camera_motion`` the base VLM estimates coarse inter-frame motion so
multi-image clips share a common camera-0 frame.
"""

import json
import logging
import math
import re
from collections import OrderedDict
from typing import List, Optional

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool, ensure_image_list
from qwen_mm_plugins_video_spatio.scene_types import (
    PerFrameExtrinsics,
    PerFrameIntrinsics,
    Reconstruction,
)

logger = logging.getLogger(__name__)

_SCENE_FOV_DEG = 60.0  # assumed horizontal field of view
_OBJ_DEPTH_CONF = 1.0
_DEFAULT_MAX_FRAMES = 64

_ESTIMATE_PROMPT = """\
You are given one first-person image. List the salient objects/surfaces useful for \
spatial reasoning (furniture, walls, doors, target objects, floor regions).
For each, give a bounding box and your best estimate of its distance from the camera in METERS.
Reply with ONLY a compact JSON object, no prose:
{"coord_system": "bbox=[x1,y1,x2,y2], 0-1000 normalized, x-first, origin top-left", "objects": [{"label": "chair", "bbox": [x1,y1,x2,y2], "depth_m": 2.3}]}
Follow that coordinate system EXACTLY: bbox is [x1, y1, x2, y2] with x FIRST then y; \
(x1,y1)=TOP-LEFT, (x2,y2)=BOTTOM-RIGHT; x=horizontal (0=left…1000=right), \
y=vertical (0=top…1000=bottom); values are 0-1000 NORMALIZED, NOT pixels. \
depth_m is a positive float (meters)."""


class ReconstructPromptTool(CPUTool):
    """Model-free instance producer: base-model estimates → instance-level Reconstruction."""

    TOOL_ABLATION_PREFIX = "tool_reconstruct"
    TOOL_PROMPT_SECTIONS = OrderedDict(
        [
            (
                "api",
                """
### tools.Reconstruct - Instance producer (model-free)

This deployment has **no external perception model**. `tools.Reconstruct.Reconstruct(frames)`
uses the base model's own **grounding + distance estimation**: it grounds salient objects and
estimates each one's distance, then emits an **instance-level** scene description (world frame:
+Y up, forward -Z, camera at origin). NO dense 3D geometry is produced.

Returns a `Reconstruction` whose useful surface is:
- `recon.instances[t]` -> list of `{id, label, bbox_1000, bbox_pixel, point_1000, depth_m, conf}`
  for frame `t` (also `recon.get_instances(t)`).
- `recon.cameras[t]` -> per-frame camera pose `{pos_bev, move_vec_bev, yaw_deg, yaw_delta_deg,
  pitch_deg, fov_deg, intrinsics}` (also `recon.get_camera(t)`).
- `recon.bev_visual(...)` -> a top-down bird's-eye-view built from instances + cameras.
- `recon.summary(t)` / `recon.summary_all()` -> a text digest of the frame(s).

**Target the question's objects** (recommended): pass
`Reconstruct(frames, targets=["toilet", "bed"])` — each target is grounded and distance-estimated
per frame, so its BEV position uses confident geometry. Then
`show(tools.Reconstruct.bbox_overlay(recon, frame=i))` to SEE the estimated boxes on that frame.

**Look from another angle** — `tools.Reconstruct.render_scene_views(recon, faces=["front","top"])`
returns 2D renders of the scene's instances from canonical viewpoints. **Default FRONT + TOP (BEV)**;
add "left"/"right" ONLY if the task still needs more faces. `show(...)` them / pass to `vlm`.
(`tools.Reconstruct.scene_html(recon)` writes an interactive 3D html — for humans/debugging.)

**3D oriented boxes** (metric size + heading in 3D): `tools.Reconstruct.ground_3d(image, targets=[...])`
returns `[{label, bbox_3d:[x,y,z, w,h,d, roll,pitch,yaw]}]` (camera coords); visualize with
`show(tools.Draw.draw_bbox_3d(image, boxes))`.
⚠️ **STRONGLY PREFER 2.5D FIRST**: `recon.instances` (bbox + `depth_m`) + `recon.bev_visual()` are
far more reliable. Use `ground_3d` ONLY when the task genuinely needs a full 3D oriented box, and
treat its numbers as COARSE — 3D grounding error is large.

**Cross-frame / metric (multi-view)** — when the camera moved between frames:
- `tools.Reconstruct.triangulate(recon, target, frame_a, frame_b)` — triangulate a target's BEV position from
  TWO views (more accurate than monocular `depth_m`); check `reliable` / `triangulation_angle_deg` (small angle
  = little camera motion = unreliable, fall back to `depth_m`).
- `tools.Reconstruct.object_world_motion(recon, target, frame_a, frame_b)` — the object's motion in the WORLD
  frame with the camera's own motion REMOVED (displacement / direction / speed): distinguishes "the object moved"
  from "the camera moved". `show(recon.bev_visual(ids=[obj_id]))` to SEE both cameras + the object across frames.
- `tools.Reconstruct.calibrate_scale(recon, target, known_size_m)` — refine metric scale from a known real size
  (e.g. a chair ~0.5 m wide) → a `scale_factor` to multiply distances by (helps ABSOLUTE distances, which
  `depth_m` alone gets wrong).

Guidance:
- Geometry is a **coarse estimate**, not metric-accurate — treat it as a scaffold and prefer
  `vlm.ask_with_thinking` reasoning for the final answer.
- `recon = tools.Reconstruct.Reconstruct(InputImages[:6])`
""",
            ),
        ]
    )

    def __init__(self, config=None, gpu_tool_max_retries: int = 3, metadata=None):
        self.MAX_FRAMES = int(getattr(config, "reconstruct_max_frames", _DEFAULT_MAX_FRAMES) or _DEFAULT_MAX_FRAMES)
        self._is_video = bool(getattr(metadata, "is_video", True))
        self._config = config
        self._vlm_module = None
        self._tracer = None  # Optional[ToolTracer], injected by ToolsModule

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    def _estimate_objects(self, image, targets: Optional[List[str]] = None) -> List[dict]:
        """Ask the base model for objects with bbox (0-1000) + depth (m).

        When ``targets`` is given (the objects the question is actually about),
        the model is required to ground and depth-estimate EACH of them first,
        then list other salient objects.
        """
        if self._vlm_module is None:
            return []
        prompt = _ESTIMATE_PROMPT
        tlist = [str(t).strip() for t in (targets or []) if str(t).strip()]
        if tlist:
            prompt = (
                "You are given one first-person image.\n"
                "FIRST, for EACH of these target objects, if it is visible, give a "
                "tight bounding box and your best estimate of its distance from the "
                "camera in METERS. Target objects: " + "; ".join(tlist) + ".\n"
                "THEN also list other salient objects/surfaces useful for spatial "
                "reasoning (furniture, walls, doors, floor regions).\n"
                "Reply with ONLY a compact JSON object, no prose:\n"
                '{"coord_system": "bbox=[x1,y1,x2,y2], 0-1000 normalized, x-first, origin top-left", '
                '"objects": [{"label": "toilet", "bbox": [x1,y1,x2,y2], "depth_m": 2.3}]}\n'
                "Follow that coordinate system EXACTLY: bbox is [x1, y1, x2, y2] with x FIRST "
                "then y; (x1,y1)=TOP-LEFT, (x2,y2)=BOTTOM-RIGHT; x=horizontal (0=left…1000=right), "
                "y=vertical (0=top…1000=bottom); 0-1000 NORMALIZED, NOT pixels. depth_m positive float. "
                "Omit a target only if it is genuinely not visible in this image."
            )
        try:
            ans = self._vlm_module.ask_with_thinking(image, prompt)
        except Exception as e:  # noqa: BLE001
            logger.warning("[ReconPrompt] estimate call failed: %s", e)
            return []
        return self._parse_objects(ans)

    @staticmethod
    def _parse_objects(text: str) -> List[dict]:
        if not text:
            return []
        m = re.search(r"\[.*\]", text, re.DOTALL)
        raw = m.group(0) if m else text
        try:
            arr = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
        out = []
        if isinstance(arr, list):
            for o in arr:
                if not isinstance(o, dict):
                    continue
                bbox = o.get("bbox")
                depth = o.get("depth_m", o.get("depth"))
                if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                    continue
                try:
                    bbox = [float(v) for v in bbox]
                    depth = float(depth)
                except (TypeError, ValueError):
                    continue
                if depth <= 0:
                    continue
                out.append({"label": str(o.get("label", "object")), "bbox": bbox, "depth_m": depth})
        return out

    # ------------------------------------------------------------------
    def Reconstruct(
        self, frames, frame_indices: Optional[List[int]] = None, targets: Optional[List[str]] = None
    ) -> Reconstruction:
        """Model-free instance-level reconstruction.

        ``targets``: the objects the QUESTION is about (e.g. ["toilet", "bed"]).
        When given, each target is grounded + depth-estimated per frame so its
        BEV position uses confident geometry.

        NOT reachable from the MCP tools: in the flattened deployment the HOST model does the
        perception and `build_scene` assembles the scene. The tools instantiate this class only for
        its pure-geometry methods (triangulate / calibrate_scale / object_world_motion /
        render_scene_views). Kept as the reference in-process path the skill's examples describe.
        """
        from qwen_mm_plugins_video_spatio.frame_image import FrameImage

        frames = ensure_image_list(frames)
        if len(frames) == 0:
            raise ValueError("Reconstruct requires at least 1 frame.")
        if len(frames) > self.MAX_FRAMES:
            frames = frames[: self.MAX_FRAMES]

        if frame_indices is None and isinstance(frames[0], FrameImage):
            frame_indices = [f.frame_index for f in frames]
        if frame_indices is None:
            frame_indices = list(range(len(frames)))
        frame_indices = frame_indices[: len(frames)]

        plain_frames = [f.image if isinstance(f, FrameImage) else f for f in frames]
        W, H = plain_frames[0].size

        # Assumed pinhole intrinsics from a fixed FOV.
        fx = 0.5 * W / math.tan(math.radians(_SCENE_FOV_DEG) / 2.0)
        fy = fx
        cx, cy = W / 2.0, H / 2.0

        N = len(plain_frames)

        # Coarse inter-frame camera motion, one VLM round-trip per frame pair.
        _motions = None
        if N > 1:
            try:
                from qwen_mm_plugins_video_spatio.camera_poses import estimate_camera_motions

                _motions, _pitch_notes = estimate_camera_motions(plain_frames, self._vlm_module)
                if _pitch_notes:
                    print("[Reconstruct] " + _pitch_notes)
            except Exception as _e:  # noqa: BLE001
                logger.warning("[ReconPrompt] camera-motion est failed: %s", _e)
                _motions = None

        # --- instances per frame: bbox -> 0..1000 (TL-BR), point, depth --------
        per_frame_estimates: List[List[dict]] = []
        instances_by_frame: dict = {}
        for i, img in enumerate(plain_frames):
            objs = self._estimate_objects(img, targets=targets)
            per_frame_estimates.append(objs)
            insts_i: List[dict] = []
            iw, ih = img.size
            for k, o in enumerate(objs):
                x1, y1, x2, y2 = o["bbox"]
                mx = max(x1, y1, x2, y2)
                if mx <= 1.0:  # 0..1
                    x1_k = x1 * 1000.0
                    y1_k = y1 * 1000.0
                    x2_k = x2 * 1000.0
                    y2_k = y2 * 1000.0
                elif mx <= 1000.0 and (iw < 1000 or ih < 1000):
                    x1_k, y1_k = x1, y1
                    x2_k, y2_k = x2, y2
                else:  # pixels → 0..1000
                    x1_k = x1 * 1000.0 / iw
                    y1_k = y1 * 1000.0 / ih
                    x2_k = x2 * 1000.0 / iw
                    y2_k = y2 * 1000.0 / ih
                x1_k, x2_k = (min(x1_k, x2_k), max(x1_k, x2_k))
                y1_k, y2_k = (min(y1_k, y2_k), max(y1_k, y2_k))
                x1_k = max(0.0, min(1000.0, float(x1_k)))
                y1_k = max(0.0, min(1000.0, float(y1_k)))
                x2_k = max(0.0, min(1000.0, float(x2_k)))
                y2_k = max(0.0, min(1000.0, float(y2_k)))
                bbox_1000 = [x1_k, y1_k, x2_k, y2_k]
                bbox_pixel = [
                    round(x1_k * iw / 1000.0),
                    round(y1_k * ih / 1000.0),
                    round(x2_k * iw / 1000.0),
                    round(y2_k * ih / 1000.0),
                ]
                cxo = (x1_k + x2_k) / 2.0
                cyo = (y1_k + y2_k) / 2.0
                insts_i.append(
                    {
                        "id": f"{o.get('label', 'obj')}_{k}",
                        "label": o.get("label", "obj"),
                        "bbox_1000": bbox_1000,
                        "bbox_pixel": bbox_pixel,
                        "point_1000": [cxo, cyo],
                        "depth_m": float(o.get("depth_m", 0.0) or 0.0),
                        "conf": float(_OBJ_DEPTH_CONF),
                        "frame_idx": int(frame_indices[i]),
                    }
                )
            instances_by_frame[int(frame_indices[i])] = insts_i

        # --- cameras: pose per frame ---------------------------------------
        if _motions is not None:
            from qwen_mm_plugins_video_spatio.camera_poses import motions_to_c2w

            c2w = motions_to_c2w(_motions)
        else:
            c2w = np.tile(np.diag([1.0, -1.0, -1.0, 1.0]).astype(np.float64), (N, 1, 1))

        cameras_by_frame: dict = {}
        prev_pos = None
        prev_yaw = None
        for i in range(N):
            M = c2w[i]
            pos_world = M[:3, 3]
            pos_bev = (float(pos_world[0]), float(pos_world[2]))  # world XZ → BEV
            R = M[:3, :3]
            fwd = R @ np.array([0.0, 0.0, -1.0])
            yaw = math.degrees(math.atan2(float(fwd[0]), -float(fwd[2])))  # +right
            pitch = math.degrees(math.atan2(float(fwd[1]), math.sqrt(float(fwd[0]) ** 2 + float(fwd[2]) ** 2)))
            if prev_pos is None:
                move_vec = (0.0, 0.0)
                dyaw = 0.0
            else:
                move_vec = (pos_bev[0] - prev_pos[0], pos_bev[1] - prev_pos[1])
                dyaw = ((yaw - prev_yaw + 540.0) % 360.0) - 180.0
            cameras_by_frame[int(frame_indices[i])] = {
                "frame_idx": int(frame_indices[i]),
                "pos_bev": list(pos_bev),
                "move_vec_bev": list(move_vec),
                "yaw_deg": yaw,
                "yaw_delta_deg": dyaw,
                "pitch_deg": pitch,
                "roll_deg": 0.0,
                "fov_deg": _SCENE_FOV_DEG,
                "intrinsics": {"fx": float(fx), "fy": float(fy), "cx": float(cx), "cy": float(cy)},
            }
            prev_pos = pos_bev
            prev_yaw = yaw

        fx_arr = np.full(N, fx)
        fy_arr = np.full(N, fy)
        cx_arr = np.full(N, cx)
        cy_arr = np.full(N, cy)
        pf_intr = PerFrameIntrinsics(fx_arr, fy_arr, cx_arr, cy_arr, frame_indices)
        pf_extr = PerFrameExtrinsics(c2w, frame_indices)
        rgb = np.stack([np.array(f.convert("RGB")) for f in plain_frames], axis=0)

        recon = Reconstruction(
            points=None,
            depth=None,
            intrinsics=pf_intr,
            extrinsics=pf_extr,
            metric_scale=1.0,
            gravity_direction=np.array([0.0, 1.0, 0.0], dtype=np.float64),
            rgb=rgb,
            frame_indices=frame_indices,
            input_images=plain_frames,
            is_video=self._is_video,
            instances=instances_by_frame,
            cameras=cameras_by_frame,
        )
        # Stash raw object estimates + target order so bbox_overlay can colour
        # objects to MATCH the BEV (tab10 by target index).
        recon._prompt_estimates = per_frame_estimates
        recon._prompt_targets = [str(t).strip() for t in (targets or []) if str(t).strip()]
        return recon

    # ------------------------------------------------------------------
    def bbox_overlay(self, recon, frame: int = 0):
        """Draw the estimated object boxes on one frame, coloured to MATCH the BEV.

        Target objects use their target-list index (the SAME order you pass to
        ``Reconstruct(targets=...)``) so an object's box here and its marker in
        ``recon.bev_visual`` share a ``tab10`` colour. Returns a ``VisualFeedback``
        for ``show()``. Non-target objects are drawn grey.
        """
        import matplotlib.pyplot as plt
        from PIL import ImageDraw

        from qwen_mm_plugins_video_spatio.visual_feedback import VisualFeedback

        imgs = getattr(recon, "_input_images", None)
        ests = getattr(recon, "_prompt_estimates", None)
        targets = getattr(recon, "_prompt_targets", []) or []
        if not imgs or ests is None or frame >= len(imgs) or frame >= len(ests):
            raise ValueError("bbox_overlay: reconstruction has no stored estimates for that frame")
        img = imgs[frame].convert("RGB").copy()
        W, H = img.size
        draw = ImageDraw.Draw(img)
        cmap = plt.colormaps.get_cmap("tab10")

        def _col(i):
            r, g, b, _ = cmap(i % 10)
            return (int(r * 255), int(g * 255), int(b * 255))

        tl = {t.lower(): i for i, t in enumerate(targets)}  # target label -> BEV colour index
        for o in ests[frame]:
            x1, y1, x2, y2 = o["bbox"]
            if max(x1, y1, x2, y2) <= 1.0:
                x1, x2, y1, y2 = x1 * W, x2 * W, y1 * H, y2 * H
            elif max(x1, y1, x2, y2) <= 1000.0 and (W < 1000 or H < 1000):
                x1, x2, y1, y2 = x1 * W / 1000.0, x2 * W / 1000.0, y1 * H / 1000.0, y2 * H / 1000.0
            lab = str(o.get("label", "object"))
            idx = tl.get(lab.lower())
            if idx is None:
                for t, i in tl.items():
                    if t in lab.lower() or lab.lower() in t:
                        idx = i
                        break
            color = _col(idx) if idx is not None else (150, 150, 150)
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
            draw.text((x1 + 3, max(0, y1 + 2)), f"{lab} ~{o.get('depth_m', '?')}m", fill=color)
        return VisualFeedback(
            img,
            source="Reconstruct.bbox_overlay",
            description=(
                f"estimated object bbox on frame {frame}; target objects coloured to match bev_visual (tab10 by index)"
            ),
        )

    # ------------------------------------------------------------------
    # 3D grounding (Qwen3-VL style oriented 3D boxes) — use SPARINGLY.
    # ------------------------------------------------------------------
    def ground_3d(self, image, targets=None, use_thinking: bool = True):
        """Model-free 3D grounding via the base VLM (Qwen3-VL-style 3D boxes).

        ⚠️ 3D grounding error is LARGE. PREFER 2.5D first — the instance-level
        ``recon.instances`` (bbox + ``depth_m``) + ``recon.bev_visual()`` are more
        reliable. Only call this when the task genuinely needs a 3D ORIENTED box
        (metric size + heading), and treat the numbers as COARSE estimates.

        Returns a list of ``{"label": str, "bbox_3d": [x,y,z, xs,ys,zs, roll,pitch,yaw]}``
        in CAMERA coords (meters; the last 3 are the model's angle units where 1.0 == 180°,
        matching ``tools.Draw.draw_bbox_3d``). Empty list on failure.
        """
        if self._vlm_module is None:
            return []
        tlist = [str(t).strip() for t in (targets or []) if str(t).strip()]
        tgt = ("the following objects: " + "; ".join(tlist)) if tlist else "all salient objects"
        prompt = (
            "Detect " + tgt + " in this image and give each one's 3D bounding box.\n"
            "Reply with ONLY a compact JSON array, no prose:\n"
            '[{"bbox_3d":[x_center,y_center,z_center,x_size,y_size,z_size,roll,pitch,yaw],"label":"category"}]\n'
            "Coordinates are CAMERA space in METERS: +x right, +y down, +z forward (into the scene). "
            "x_size/y_size/z_size are the object's metric dimensions; roll/pitch/yaw are its rotation."
        )
        try:
            ask = self._vlm_module.ask_with_thinking if use_thinking else self._vlm_module.ask
            ans = ask(image, prompt)
        except Exception as e:  # noqa: BLE001
            logger.warning("[ground_3d] VLM call failed: %s", e)
            return []
        return self._parse_bbox_3d(ans)

    @staticmethod
    def _parse_bbox_3d(text: str) -> List[dict]:
        """Parse Qwen3-VL 3D-box JSON: [{'bbox_3d':[9 floats],'label':str}, ...]."""
        if not text:
            return []
        if "```json" in text:
            s = text.find("```json") + 7
            e = text.find("```", s)
            raw = text[s:e].strip() if e != -1 else text[s:].strip()
        else:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            raw = m.group(0) if m else text
        try:
            arr = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
        if isinstance(arr, dict):
            arr = [arr]
        out = []
        for o in arr if isinstance(arr, list) else []:
            if not isinstance(o, dict):
                continue
            box = o.get("bbox_3d") or o.get("bbox3d") or o.get("box_3d")
            if not (isinstance(box, (list, tuple)) and len(box) == 9):
                continue
            try:
                box = [float(v) for v in box]
            except (TypeError, ValueError):
                continue
            out.append({"label": str(o.get("label", "object")), "bbox_3d": box})
        return out

    # ------------------------------------------------------------------
    # Scene render skills (ADDITIVE) — common render frame everywhere:
    #   right-handed, x=right, y=UP, z=forward (into scene), ego at origin, meters.
    # ------------------------------------------------------------------
    def _scene_boxes(self, recon, boxes_3d=None, frame=None):
        """Normalise a scene into the common render frame.

        Returns a list of ``{label, center:[x,y,z], size:[w,h,d], yaw_deg, color}``
        where ``color`` is an ``0xRRGGBB`` int. Source is either 3D-grounding boxes
        (``boxes_3d`` = output of :meth:`ground_3d`, CAMERA coords) or the 2.5D
        per-frame instances (``recon.get_instances(frame)``).
        """
        import zlib

        palette = [0x2196F3, 0x4CAF50, 0xFF9800, 0x9C27B0, 0xE91E63, 0x00BCD4, 0x8BC34A, 0xFF5722]

        def _color(label):
            h = zlib.crc32(str(label).encode("utf-8"))
            return palette[h % len(palette)]

        boxes = []
        if boxes_3d:
            for b in boxes_3d:
                bb = b.get("bbox_3d") if isinstance(b, dict) else None
                if not (isinstance(bb, (list, tuple)) and len(bb) == 9):
                    continue
                try:
                    x, y, z, xs, ys, zs, _roll, _pitch, yaw = [float(v) for v in bb]
                except (TypeError, ValueError):
                    continue
                lab = str(b.get("label", "object"))
                boxes.append(
                    {
                        "label": lab,
                        # camera (+x right, +y DOWN, +z forward) -> render (x, -y, -z)
                        "center": [x, -y, -z],
                        "size": [xs, ys, zs],
                        "yaw_deg": -(yaw * 180.0),  # 1.0 == 180deg; flip for +y-down
                        "color": _color(lab),
                    }
                )
            return boxes

        # From 2.5D instances of one frame.
        if frame is None:
            fis = getattr(recon, "frame_indices", None)
            frame = int(fis[0]) if fis else 0
        try:
            insts = recon.get_instances(frame) or []
        except Exception:  # noqa: BLE001
            insts = []
        try:
            cam = recon.get_camera(frame) or {}
        except Exception:  # noqa: BLE001
            cam = {}
        F = float(cam.get("fov_deg") or _SCENE_FOV_DEG)
        for inst in insts:
            pt = inst.get("point_1000") or [500.0, 500.0]
            bbox = inst.get("bbox_1000") or [0.0, 0.0, 0.0, 0.0]
            try:
                d = float(inst.get("depth_m", 0.0) or 0.0)
                px = float(pt[0])
                py = float(pt[1])
                x1, y1, x2, y2 = [float(v) for v in bbox]
            except (TypeError, ValueError):
                continue
            bx = (px / 1000.0 - 0.5) * F  # deg, +right
            by = (0.5 - py / 1000.0) * F  # deg, +up
            center_x = d * math.tan(math.radians(bx))
            center_y = d * math.tan(math.radians(by))
            center_z = d
            wfrac = (x2 - x1) / 1000.0
            hfrac = (y2 - y1) / 1000.0
            w = 2.0 * d * math.tan(math.radians(F * wfrac / 2.0))
            h = 2.0 * d * math.tan(math.radians(F * hfrac / 2.0))
            depth_sz = max(0.3, 0.5 * min(w, h))
            lab = str(inst.get("label", "object"))
            boxes.append(
                {
                    "label": lab,
                    "center": [center_x, center_y, center_z],
                    "size": [abs(w), abs(h), depth_sz],
                    "yaw_deg": 0.0,
                    "color": _color(lab),
                }
            )
        return boxes

    def render_scene_views(self, recon=None, boxes_3d=None, frame=None, faces=("front", "top")):
        """Render orthographic 2D views of the scene as ``VisualFeedback`` images.

        Draws each scene box (from :meth:`_scene_boxes`) as a wireframe cuboid on
        the requested orthographic ``faces`` (``front``/``top``|``bev``/``left``/
        ``right``). Returns a list of ``VisualFeedback`` (one per face), or a single
        "no instances" note image when the scene is empty.
        """
        import io

        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.lines import Line2D
        from PIL import Image

        from qwen_mm_plugins_video_spatio.visual_feedback import VisualFeedback

        boxes = self._scene_boxes(recon, boxes_3d=boxes_3d, frame=frame)

        def _hex(c):
            return "#%06x" % (int(c) & 0xFFFFFF)

        def _corners(box):
            cx, cy, cz = box["center"]
            w, h, d = box["size"]
            a = math.radians(box.get("yaw_deg", 0.0))
            ca, sa = math.cos(a), math.sin(a)
            pts, signs = [], []
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        lx = sx * w / 2.0
                        ly = sy * h / 2.0
                        lz = sz * d / 2.0
                        X = lx * ca + lz * sa  # rotate half-size about +Y
                        Z = -lx * sa + lz * ca
                        Y = ly
                        pts.append((cx + X, cy + Y, cz + Z))
                        signs.append((sx, sy, sz))
            return pts, signs

        def _edges(signs):
            e = []
            n = len(signs)
            for i in range(n):
                for j in range(i + 1, n):
                    if sum(1 for k in range(3) if signs[i][k] != signs[j][k]) == 1:
                        e.append((i, j))
            return e

        def _project(face, p):
            x, y, z = p
            if face in ("top", "bev"):
                return (x, z)  # x right, z UP-in-image (forward)
            if face == "left":
                return (z, y)  # z right, y up
            if face == "right":
                return (-z, y)  # -z right, y up
            return (x, y)  # front: x right, y up

        face_meta = {
            "front": ("x (m, right)", "y (m, up)", "FRONT (cam looks -Z)"),
            "top": ("x (m, right)", "z (m, forward)", "TOP / BEV"),
            "bev": ("x (m, right)", "z (m, forward)", "TOP / BEV"),
            "left": ("z (m, forward)", "y (m, up)", "LEFT side"),
            "right": ("-z (m, backward)", "y (m, up)", "RIGHT side"),
        }

        def _fig_to_pil(fig):
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
            buf.seek(0)
            return Image.open(buf).convert("RGB")

        if not boxes:
            fig = Figure(figsize=(6, 6))
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5, "no instances in scene", ha="center", va="center", transform=ax.transAxes, fontsize=14)
            ax.set_axis_off()
            return [
                VisualFeedback(
                    _fig_to_pil(fig), source="Reconstruct.view_empty", description="scene has no instances to render"
                )
            ]

        out = []
        for face in faces:
            xl, yl, title = face_meta.get(face, face_meta["front"])
            fig = Figure(figsize=(6, 6))
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            for box in boxes:
                pts, signs = _corners(box)
                proj = [_project(face, p) for p in pts]
                col = _hex(box["color"])
                for i, j in _edges(signs):
                    ax.add_line(Line2D([proj[i][0], proj[j][0]], [proj[i][1], proj[j][1]], color=col, linewidth=1.5))
                cproj = _project(face, box["center"])
                ax.text(cproj[0], cproj[1], box["label"], color=col, fontsize=8, ha="center", va="center", zorder=6)
            # ego / origin marker (small triangle at (0,0))
            ax.plot([0], [0], marker="^", markersize=13, color="black", zorder=5)
            ax.set_aspect("equal", adjustable="datalim")
            ax.grid(True, linestyle=":", alpha=0.5)
            ax.set_xlabel(xl)
            ax.set_ylabel(yl)
            ax.set_title(title)
            out.append(
                VisualFeedback(
                    _fig_to_pil(fig),
                    source="Reconstruct.view_%s" % face,
                    description="orthographic %s view of %d scene box(es)" % (title, len(boxes)),
                )
            )
        return out

    def scene_html(self, recon=None, boxes_3d=None, out_path=None, max_img=640):
        """Write a self-contained interactive three.js HTML of the scene.

        Human/debug artifact (the agent cannot view HTML). Builds the scene from
        :meth:`_scene_boxes` + ``recon.cameras`` + base64-embedded frames, emits a
        minimal three.js viewer (orbit controls, box per object, grid floor, axes)
        to ``out_path`` and returns ``{"path", "num_objects", "note"}``.
        """
        import base64
        import io
        import json as _json
        import os
        import tempfile

        boxes = self._scene_boxes(recon, boxes_3d=boxes_3d)

        cams = []
        cams_d = getattr(recon, "cameras", None)
        if isinstance(cams_d, dict):
            for k in sorted(cams_d.keys(), key=lambda v: str(v)):
                c = cams_d[k] or {}
                pos = c.get("pos_bev", [0.0, 0.0]) or [0.0, 0.0]
                try:
                    cams.append(
                        {
                            "frame": int(k),
                            "pos": [float(pos[0]), 0.0, float(pos[1])],
                            "yaw_deg": float(c.get("yaw_deg", 0.0)),
                        }
                    )
                except (TypeError, ValueError):
                    continue

        frames_b64 = []
        imgs = getattr(recon, "_input_images", None) or []
        for im in imgs:
            try:
                thumb = im.convert("RGB").copy()
                thumb.thumbnail((int(max_img), int(max_img)))
                b = io.BytesIO()
                thumb.save(b, format="JPEG", quality=82)
                frames_b64.append("data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode("ascii"))
            except Exception:  # noqa: BLE001
                continue

        scene = {
            "boxes": [
                {
                    "label": bx["label"],
                    "center": bx["center"],
                    "size": bx["size"],
                    "yaw_deg": bx["yaw_deg"],
                    "color": int(bx["color"]) & 0xFFFFFF,
                }
                for bx in boxes
            ],
            "cameras": cams,
        }
        n = len(boxes)

        if out_path is None:
            out_path = os.path.join(tempfile.gettempdir(), "scene_%d.html" % (id(recon) & 0xFFFFFFFF))

        template = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Scene (__NUM__ objects)</title>
<style>body{margin:0;overflow:hidden;font-family:sans-serif;background:#1a1a1a}
#info{position:absolute;top:8px;left:8px;color:#fff;background:rgba(0,0,0,.55);padding:6px 10px;font-size:12px;border-radius:4px}
#frames{position:absolute;bottom:8px;left:8px}
#frames img{height:90px;margin-right:4px;border:1px solid #888;border-radius:3px}</style>
</head><body>
<div id="info">Scene: __NUM__ objects &mdash; drag to orbit, scroll to zoom (render frame: x=right, y=up, z=forward)</div>
<div id="frames"></div>
<script src="https://unpkg.com/three@0.128.0/build/three.min.js"></script>
<script src="https://unpkg.com/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
var SCENE = __SCENE__;
var IMAGES = __IMAGES__;
var scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a1a);
var camera = new THREE.PerspectiveCamera(60, window.innerWidth/window.innerHeight, 0.01, 2000);
camera.position.set(4, 4, 8);
var renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);
var controls = new THREE.OrbitControls(camera, renderer.domElement);
scene.add(new THREE.GridHelper(40, 40, 0x888888, 0x444444));
scene.add(new THREE.AxesHelper(2));
scene.add(new THREE.AmbientLight(0xffffff, 0.75));
var dl = new THREE.DirectionalLight(0xffffff, 0.6); dl.position.set(5, 10, 7); scene.add(dl);
SCENE.boxes.forEach(function(b){
  var g = new THREE.BoxGeometry(b.size[0], b.size[1], b.size[2]);
  var m = new THREE.MeshLambertMaterial({color:b.color, transparent:true, opacity:0.55});
  var mesh = new THREE.Mesh(g, m);
  mesh.position.set(b.center[0], b.center[1], b.center[2]);
  mesh.rotation.y = b.yaw_deg * Math.PI / 180.0;
  scene.add(mesh);
  var edges = new THREE.LineSegments(new THREE.EdgesGeometry(g),
                                     new THREE.LineBasicMaterial({color:b.color}));
  mesh.add(edges);
});
var fr = document.getElementById('frames');
IMAGES.forEach(function(src){ var im = document.createElement('img'); im.src = src; fr.appendChild(im); });
function animate(){ requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }
animate();
window.addEventListener('resize', function(){
  camera.aspect = window.innerWidth/window.innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
</script></body></html>"""

        html = (
            template.replace("__NUM__", str(n))
            .replace("__SCENE__", _json.dumps(scene))
            .replace("__IMAGES__", _json.dumps(frames_b64))
        )
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return {"path": out_path, "num_objects": n, "note": "open in a browser"}

    # ------------------------------------------------------------------
    # Geometry helpers (ADDITIVE) — mirror mobile_expert without importing it.
    #   world = three.js Y-up; BEV plane = (x=right, z=forward); camera-0 at origin.
    # ------------------------------------------------------------------
    @staticmethod
    def _bearing_deg(inst: dict, cam: Optional[dict]) -> float:
        """Horizontal bearing of an instance from camera forward, deg (+ = right).

        ``(point_1000[0]/1000 - 0.5) * fov`` — same as mobile_expert._inst_angle_deg.
        """
        px = float((inst.get("point_1000") or [500.0, 500.0])[0])
        fov = float((cam or {}).get("fov_deg") or _SCENE_FOV_DEG)
        return (px / 1000.0 - 0.5) * fov

    @staticmethod
    def _world_xz(inst: dict, cam: Optional[dict]) -> tuple:
        """World BEV (x, z) meters of an instance (matches mobile_expert._inst_bev_xz).

        ego: ex = depth*sin(bearing) (right), ez = depth*cos(bearing) (forward);
        world: wx = px + ez*sin(yaw) + ex*cos(yaw);
               wz = pz - ez*cos(yaw) + ex*sin(yaw).
        """
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        bearing = math.radians(ReconstructPromptTool._bearing_deg(inst, cam))
        ex = depth * math.sin(bearing)  # right
        ez = depth * math.cos(bearing)  # forward
        if not cam:
            return (ex, ez)
        yaw = math.radians(float(cam.get("yaw_deg", 0.0)))
        px, pz = cam.get("pos_bev", (0.0, 0.0))
        wx = float(px) + ez * math.sin(yaw) + ex * math.cos(yaw)
        wz = float(pz) - ez * math.cos(yaw) + ex * math.sin(yaw)
        return (wx, wz)

    @staticmethod
    def _view_dir(inst: dict, cam: Optional[dict]) -> tuple:
        """Unit world-XZ direction from the camera toward the instance.

        fwd=(sin yaw, -cos yaw); right=(cos yaw, sin yaw);
        d = right*sin(bearing) + fwd*cos(bearing).
        """
        yaw = math.radians(float((cam or {}).get("yaw_deg", 0.0)))
        bearing = math.radians(ReconstructPromptTool._bearing_deg(inst, cam))
        fx, fz = math.sin(yaw), -math.cos(yaw)
        rx, rz = math.cos(yaw), math.sin(yaw)
        return (rx * math.sin(bearing) + fx * math.cos(bearing), rz * math.sin(bearing) + fz * math.cos(bearing))

    @staticmethod
    def _dir8(angle_deg: float) -> str:
        """Map a bearing (deg, +right, 0=forward) to an 8-way label."""
        a = ((angle_deg + 180.0) % 360.0) - 180.0
        aa = abs(a)
        if aa <= 22.5:
            return "forward"
        if aa >= 157.5:
            return "backward"
        side = "right" if a > 0 else "left"
        if aa <= 67.5:
            return "forward-%s" % side
        if aa <= 112.5:
            return side
        return "backward-%s" % side

    @staticmethod
    def _find_inst(insts, target):
        """First instance whose label/id case-insensitively matches ``target``."""
        t = str(target).strip().lower()
        for it in insts or []:
            lab = str(it.get("label", "")).lower()
            iid = str(it.get("id", "")).lower()
            if t == lab or t in lab or lab in t or t in iid:
                return it
        return None

    # ------------------------------------------------------------------
    # 2-view triangulation (ADDITIVE) — more accurate than monocular depth_m.
    # ------------------------------------------------------------------
    def triangulate(self, recon, target, frame_a, frame_b):
        """Two-view triangulation of ``target`` from frames ``frame_a`` and ``frame_b``.

        More accurate than a single-frame ``depth_m`` when the camera has moved:
        builds the world view-ray from each camera (position ``pos_bev`` + bearing
        direction) and solves the 2D least-squares intersection (closest point
        between the two rays). ``P = pos_a + t*d_a``.

        A LARGER triangulation angle (the parallax between the two view rays) means
        a MORE reliable estimate; a near-0 angle (little camera motion between the
        two frames) makes the intersection ill-conditioned and UNRELIABLE — prefer
        monocular ``depth_m`` in that case.

        Returns ``{"label", "world_xz":[x,z], "depth_from_a", "depth_from_b",
        "baseline_m", "triangulation_angle_deg", "reliable"}`` (``reliable`` is angle>=8°).
        """
        cam_a = recon.get_camera(frame_a) or {}
        cam_b = recon.get_camera(frame_b) or {}
        inst_a = self._find_inst(recon.get_instances(frame_a), target)
        inst_b = self._find_inst(recon.get_instances(frame_b), target)
        label = str(target)
        if inst_a is None or inst_b is None:
            return {
                "label": label,
                "world_xz": None,
                "depth_from_a": None,
                "depth_from_b": None,
                "baseline_m": None,
                "triangulation_angle_deg": None,
                "reliable": False,
                "note": "target not present in both frames",
            }
        label = str(inst_a.get("label", target))

        d_a = self._view_dir(inst_a, cam_a)
        d_b = self._view_dir(inst_b, cam_b)
        pa = [float(v) for v in (cam_a.get("pos_bev") or (0.0, 0.0))]
        pb = [float(v) for v in (cam_b.get("pos_bev") or (0.0, 0.0))]

        # Closed-form closest point between two 2D rays (both dirs are unit).
        B = d_a[0] * d_b[0] + d_a[1] * d_b[1]  # d_a . d_b
        w0 = (pa[0] - pb[0], pa[1] - pb[1])
        D = d_a[0] * w0[0] + d_a[1] * w0[1]  # d_a . w0
        E = d_b[0] * w0[0] + d_b[1] * w0[1]  # d_b . w0
        denom = 1.0 - B * B  # sin^2(angle)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, B))))

        if abs(denom) < 1e-9:  # near-parallel rays
            # Ill-conditioned (little/no camera motion) → fall back to depth_m.
            t = float(inst_a.get("depth_m", 0.0) or 0.0)
            s = float(inst_b.get("depth_m", 0.0) or 0.0)
        else:
            t = (B * E - D) / denom
            s = (E - B * D) / denom
        pt_a = (pa[0] + t * d_a[0], pa[1] + t * d_a[1])
        pt_b = (pb[0] + s * d_b[0], pb[1] + s * d_b[1])
        world = ((pt_a[0] + pt_b[0]) / 2.0, (pt_a[1] + pt_b[1]) / 2.0)
        baseline = math.hypot(pb[0] - pa[0], pb[1] - pa[1])

        return {
            "label": label,
            "world_xz": [round(world[0], 4), round(world[1], 4)],
            "depth_from_a": round(float(t), 4),
            "depth_from_b": round(float(s), 4),
            "baseline_m": round(float(baseline), 4),
            "triangulation_angle_deg": round(float(angle), 2),
            "reliable": bool(angle >= 8.0),
        }

    # ------------------------------------------------------------------
    # Object world-frame motion (ADDITIVE) — camera ego-motion removed.
    # ------------------------------------------------------------------
    def object_world_motion(self, recon, target, frame_a=None, frame_b=None):
        """Motion of ``target`` in the WORLD frame, camera ego-motion removed.

        Each frame's instance is placed via its OWN camera pose, so both world
        positions live in the common camera-0 frame — their difference IS the
        object's world motion (the camera's own movement is already accounted for).
        ``frame_a``/``frame_b`` default to the first/last frame where the target
        is present.

        To SEE both cameras + the object across the two frames on the top-down BEV,
        ``show(recon.bev_visual(ids=[<the object id>]))`` (the existing 2-frame
        camera+object BEV).

        Returns ``{"label", "pos_a", "pos_b", "displacement_m", "move_vec",
        "direction", "is_moving", "camera_moved_m", "frames"}``.
        """
        fis = list(getattr(recon, "frame_indices", []) or sorted((getattr(recon, "instances", {}) or {}).keys()))
        present = [fi for fi in fis if self._find_inst(recon.get_instances(fi), target) is not None]
        if frame_a is None:
            frame_a = present[0] if present else (fis[0] if fis else 0)
        if frame_b is None:
            frame_b = present[-1] if present else frame_a

        inst_a = self._find_inst(recon.get_instances(frame_a), target)
        inst_b = self._find_inst(recon.get_instances(frame_b), target)
        cam_a = recon.get_camera(frame_a) or {}
        cam_b = recon.get_camera(frame_b) or {}
        label = str(target)
        if inst_a is None or inst_b is None:
            return {
                "label": label,
                "pos_a": None,
                "pos_b": None,
                "displacement_m": None,
                "move_vec": None,
                "direction": None,
                "is_moving": False,
                "camera_moved_m": None,
                "frames": [frame_a, frame_b],
                "note": "target not present in both frames",
            }
        label = str(inst_a.get("label", target))

        wa = self._world_xz(inst_a, cam_a)
        wb = self._world_xz(inst_b, cam_b)
        dx, dz = wb[0] - wa[0], wb[1] - wa[1]
        dist = math.hypot(dx, dz)
        # world move vector: +x=right, +z=forward → bearing 0=forward, +right.
        direction = self._dir8(math.degrees(math.atan2(dx, dz)))
        ca = [float(v) for v in (cam_a.get("pos_bev") or (0.0, 0.0))]
        cb = [float(v) for v in (cam_b.get("pos_bev") or (0.0, 0.0))]
        cam_moved = math.hypot(cb[0] - ca[0], cb[1] - ca[1])

        return {
            "label": label,
            "pos_a": [round(wa[0], 4), round(wa[1], 4)],
            "pos_b": [round(wb[0], 4), round(wb[1], 4)],
            "displacement_m": round(float(dist), 4),
            "move_vec": [round(float(dx), 4), round(float(dz), 4)],
            "direction": direction,
            "is_moving": bool(dist > 0.3),
            "camera_moved_m": round(float(cam_moved), 4),
            "frames": [frame_a, frame_b],
        }

    # ------------------------------------------------------------------
    # Metric-scale calibration (ADDITIVE) — refine scale from a known size.
    # ------------------------------------------------------------------
    def calibrate_scale(self, recon, target, known_size_m, dim="width", frame=None):
        """Refine the metric scale from a target's known real-world size.

        Observed size = ``2*depth_m*tan(rad(fov * bbox_frac/2))`` where ``bbox_frac``
        is ``(x2-x1)/1000`` for ``dim="width"`` or ``(y2-y1)/1000`` for
        ``dim="height"``; ``scale_factor = known_size_m / max(observed, 1e-6)``.
        Multiply reconstructed depths / distances by ``scale_factor`` to correct them.

        Returns ``{"target", "observed_m", "known_m", "scale_factor", "note"}``.
        """
        fis = list(getattr(recon, "frame_indices", []) or sorted((getattr(recon, "instances", {}) or {}).keys()))
        if frame is None:
            present = [fi for fi in fis if self._find_inst(recon.get_instances(fi), target) is not None]
            frame = present[0] if present else (fis[0] if fis else 0)

        inst = self._find_inst(recon.get_instances(frame), target)
        cam = recon.get_camera(frame) or {}
        label = str(inst.get("label", target)) if inst else str(target)
        if inst is None:
            return {
                "target": label,
                "observed_m": None,
                "known_m": float(known_size_m),
                "scale_factor": None,
                "note": "target not found; cannot calibrate scale",
            }

        fov = float(cam.get("fov_deg") or _SCENE_FOV_DEG)
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        bbox = [float(v) for v in (inst.get("bbox_1000") or [0.0, 0.0, 0.0, 0.0])]
        if str(dim).lower() == "height":
            frac = (bbox[3] - bbox[1]) / 1000.0
        else:
            frac = (bbox[2] - bbox[0]) / 1000.0
        observed = 2.0 * depth * math.tan(math.radians(fov * frac / 2.0))
        observed = max(observed, 1e-6)

        try:
            from qwen_mm_plugins_video_spatio.geometry import GeometryUtils

            factor = float(GeometryUtils.scale_from_known_size(observed, float(known_size_m))["scale_factor"])
        except Exception:  # noqa: BLE001
            factor = float(known_size_m) / observed

        return {
            "target": label,
            "observed_m": round(observed, 4),
            "known_m": float(known_size_m),
            "scale_factor": round(factor, 4),
            "note": "multiply depths/distances by scale_factor",
        }
