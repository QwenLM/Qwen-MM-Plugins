"""Keyframe selector (model-free): query-relevant keyframe selection from video.

Selection is computed ENTIRELY from the instance-level, model-free reconstruction
(``recon.get_instances`` / ``recon.cameras``) plus optional base-model fallbacks
(``vlm.locate`` / ``vlm.ask``). There is no external perception model: no
segmentation masks and no dense 3D reconstruction are used.

- ``recon.get_instances(t)`` -> per-frame ``{id, label, bbox_1000, point_1000,
  depth_m, conf, ...}`` — used to find frames where a target is best visible.
- ``recon.get_camera(t)`` / ``recon.cameras`` -> per-frame ``{pos_bev, yaw_deg,
  ...}`` — used to pick frames with diverse viewpoints.
- ``vlm.locate`` / ``vlm.ask`` -> base-model fallback when a target is not among
  the reconstructed instances.

Public method names and the KEYS of returned values are preserved so callers are
unchanged. The VLM handle is injected via ``set_vlm_module`` (mirrors Reconstruct).
"""

from typing import List, Union

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class KeyframeSelector(CPUTool):
    """Select query-relevant keyframes from video (model-free).

    Helps avoid processing redundant frames by selecting the most informative
    subset for a given spatial reasoning task, using the instance-level scene
    and camera track (with an optional base-model fallback).
    """

    TOOL_PROMPT_DESCRIPTION = """\
### tools.Keyframe — Keyframe Selection (CPU)

Select informative keyframes from video for efficient spatial reasoning. Selection
runs on the model-free reconstruction (`recon.get_instances` / `recon.cameras`) with
an optional base-model fallback (`vlm.locate` / `vlm.ask`).

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `select_uniform` | `(total_frames, n=8)` | `list[int]` | Uniformly spaced frame indices |
| `select_by_motion` | `(recon, n=8)` | `list[int]` | Frames with the most diverse camera viewpoints |
| `select_by_coverage` | `(recon, label, n=8, images=None)` | `list[int]` | Frames where a target object is best visible |
| `select_by_covisibility` | `(recon, labels, n=8)` | `list[int]` | Frames where multiple objects are co-visible |

**Example — diverse viewpoints, then inspect a target's boxes**
```python
recon = tools.Reconstruct.Reconstruct(InputImages[:32], targets=["chair", "table"])
key_frames = tools.Keyframe.select_by_motion(recon, n=8)
# Or: frames where the chair is largest / most centered / nearest
best = tools.Keyframe.select_by_coverage(recon, "chair", n=4)
for fi in best:
    insts = recon.get_instances(fi)          # {label, bbox_1000, point_1000, depth_m, ...}
    answer = vlm.ask(InputImages[fi], "Describe the chair's position.")
```
"""

    def __init__(self):
        self._vlm_module = None
        self._tracer = None

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _frame_indices(recon) -> List[int]:
        fis = getattr(recon, "frame_indices", None)
        if fis:
            return list(fis)
        insts = getattr(recon, "instances", None) or {}
        cams = getattr(recon, "cameras", None) or {}
        return sorted(set(list(insts.keys()) + list(cams.keys())))

    @staticmethod
    def _match(inst: dict, target: Union[int, str]) -> bool:
        """Label/id match for a target (string) against an instance."""
        if isinstance(target, int):
            return False
        t = str(target).strip().lower()
        if not t:
            return False
        lab = str(inst.get("label", "")).lower()
        iid = str(inst.get("id", "")).lower()
        return t == lab or t in lab or lab in t or t in iid

    @classmethod
    def _target_instances(cls, insts: List[dict], target: Union[int, str]) -> List[dict]:
        if isinstance(target, int):
            return [insts[target]] if 0 <= target < len(insts) else []
        return [it for it in insts if cls._match(it, target)]

    @staticmethod
    def _visibility_score(inst: dict) -> float:
        """Rank an instance by how well the object is seen in the frame.

        Combines confidence, bbox area (0..1), centered-ness, and nearer depth
        (nearer = higher). Larger is better.
        """
        bbox = inst.get("bbox_1000") or [0.0, 0.0, 0.0, 0.0]
        x1, y1, x2, y2 = (float(v) for v in bbox)
        area = max(0.0, (x2 - x1)) * max(0.0, (y2 - y1)) / (1000.0 * 1000.0)  # 0..1
        pt = inst.get("point_1000") or [(x1 + x2) / 2.0, (y1 + y2) / 2.0]
        cx, cy = float(pt[0]), float(pt[1])
        # centered-ness: 1.0 at image center, ->0 at the corners.
        off = ((cx - 500.0) ** 2 + (cy - 500.0) ** 2) ** 0.5 / (500.0 * 1.41421356)
        centered = max(0.0, 1.0 - off)
        conf = float(inst.get("conf", 1.0) or 1.0)
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        near = 1.0 / (1.0 + depth)  # 1.0 at 0m, decays with distance
        return conf * (area + 0.3 * centered + 0.3 * near)

    def _frame_image(self, recon, images, frame: int):
        """Best-effort PIL image for a frame (for VLM fallbacks)."""

        def _plain(x):
            return x.image if hasattr(x, "image") else x

        fis = self._frame_indices(recon)
        if images:
            imgs = list(images)
            if frame in fis and fis.index(frame) < len(imgs):
                return _plain(imgs[fis.index(frame)])
            return _plain(imgs[0]) if imgs else None
        stored = getattr(recon, "_input_images", None)
        if stored:
            if frame in fis and fis.index(frame) < len(stored):
                return stored[fis.index(frame)]
            return stored[0]
        return None

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    @staticmethod
    def select_uniform(total_frames: int, n: int = 8) -> List[int]:
        """Select n uniformly spaced frame indices from [0, total_frames)."""
        if total_frames <= 0:
            return []
        n = min(n, total_frames)
        if n == 1:
            return [total_frames // 2]
        indices = np.linspace(0, total_frames - 1, n, dtype=int).tolist()
        return sorted(set(indices))

    def select_by_motion(self, recon, n: int = 8) -> List[int]:
        """Select n frames with the most diverse camera viewpoints.

        Uses greedy farthest-point sampling over the per-frame camera BEV pose
        (``recon.cameras[t]['pos_bev']`` plus a yaw term) to maximize viewpoint
        coverage. Falls back to uniform sampling of ``recon.frame_indices`` when
        no camera track is available.
        """
        frames = self._frame_indices(recon)
        if len(frames) <= n:
            return list(frames)

        cams = getattr(recon, "cameras", None) or {}
        feats = []
        have_cams = True
        for fi in frames:
            cam = cams.get(fi) if isinstance(cams, dict) else None
            if cam is None and hasattr(recon, "get_camera"):
                try:
                    cam = recon.get_camera(fi)
                except Exception:  # noqa: BLE001
                    cam = None
            if not cam:
                have_cams = False
                break
            px, pz = cam.get("pos_bev", (0.0, 0.0))
            yaw = float(cam.get("yaw_deg", 0.0))
            # Encode yaw on a small circle (~1m radius) so orientation spreads too.
            yr = np.radians(yaw)
            feats.append([float(px), float(pz), np.cos(yr), np.sin(yr)])

        if not have_cams or not feats:
            # No usable camera track -> uniform coverage over the frame list.
            picks = np.linspace(0, len(frames) - 1, n, dtype=int).tolist()
            return sorted({frames[i] for i in picks})

        positions = np.asarray(feats, dtype=float)
        selected = [0]
        remaining = set(range(1, len(frames)))
        for _ in range(n - 1):
            if not remaining:
                break
            sel_positions = positions[selected]
            best_idx, best_dist = -1, -1.0
            for idx in remaining:
                dists = np.linalg.norm(sel_positions - positions[idx], axis=1)
                min_dist = float(dists.min())
                if min_dist > best_dist:
                    best_dist, best_idx = min_dist, idx
            if best_idx >= 0:
                selected.append(best_idx)
                remaining.discard(best_idx)

        return sorted(frames[i] for i in selected)

    def select_by_coverage(self, recon, label: Union[int, str], n: int = 8, images=None) -> List[int]:
        """Select n frames where the target object is best visible.

        Scans ``recon.get_instances(frame)`` for the target label and ranks
        frames by a visibility score (confidence, bbox area, centered-ness, and
        nearer distance). If NO frame has the target among its instances, falls
        back to ``vlm.locate`` per candidate frame image and keeps the frames
        where the base model does locate it.
        """
        frames = self._frame_indices(recon)
        if len(frames) <= n:
            return list(frames)

        frame_scores = []
        for fi in frames:
            try:
                insts = recon.get_instances(fi)
            except Exception:  # noqa: BLE001
                insts = []
            hits = self._target_instances(insts, label)
            score = max((self._visibility_score(it) for it in hits), default=0.0)
            frame_scores.append((fi, score))

        positive = [fs for fs in frame_scores if fs[1] > 0.0]
        if positive:
            positive.sort(key=lambda x: x[1], reverse=True)
            return sorted(fs[0] for fs in positive[:n])

        # VLM fallback: probe frames with vlm.locate and keep the ones that hit.
        lbl = label if isinstance(label, str) else f"object_{label}"
        located = []
        if self._vlm_module is not None:
            for fi in frames:
                img = self._frame_image(recon, images, fi)
                if img is None:
                    continue
                try:
                    ans = self._vlm_module.locate(
                        img,
                        f"Give the center of the {lbl} as (x=.., y=..) in 0-1000 normalized scale, or 'Not visible'.",
                    )
                except Exception:  # noqa: BLE001
                    continue
                if ans and "not visible" not in ans.strip().lower():
                    located.append(fi)
                if len(located) >= n:
                    break
        if located:
            return sorted(located[:n])

        # Nothing found anywhere -> uniform fallback so callers still get frames.
        picks = np.linspace(0, len(frames) - 1, n, dtype=int).tolist()
        return sorted({frames[i] for i in picks})

    def select_by_covisibility(self, recon, labels: List[Union[int, str]], n: int = 8) -> List[int]:
        """Select n frames where the most target objects are co-visible.

        For each frame, scans ``recon.get_instances(frame)`` and counts how many
        of ``labels`` appear (label/id match), then ranks by (visible_count,
        total_visibility_score). Prefers frames where all requested objects show
        up together.
        """
        frames = self._frame_indices(recon)
        if len(frames) <= n:
            return list(frames)

        frame_scores = []
        for fi in frames:
            try:
                insts = recon.get_instances(fi)
            except Exception:  # noqa: BLE001
                insts = []
            visible_count = 0
            total_score = 0.0
            for label in labels:
                hits = self._target_instances(insts, label)
                if hits:
                    visible_count += 1
                    total_score += max(self._visibility_score(it) for it in hits)
            frame_scores.append((fi, visible_count, total_score))

        frame_scores.sort(key=lambda x: (x[1], x[2]), reverse=True)
        if frame_scores and frame_scores[0][1] == 0:
            # None of the labels appear in any frame -> uniform fallback.
            picks = np.linspace(0, len(frames) - 1, n, dtype=int).tolist()
            return sorted({frames[i] for i in picks})
        selected = [fs[0] for fs in frame_scores[:n]]
        return sorted(selected)

    def __repr__(self) -> str:
        return "KeyframeSelector(methods: select_uniform, select_by_motion, select_by_coverage, select_by_covisibility)"
