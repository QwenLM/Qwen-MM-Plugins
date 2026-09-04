"""Counting expert (model-free).

Per-frame and cross-frame deduplicated object counting computed ENTIRELY from the
instance-level, model-free reconstruction:

- ``recon.get_instances(t)`` — per-frame ``{id, label, bbox_1000, point_1000, depth_m, ...}``.
- ``recon.get_camera(t)``    — per-frame ``{pos_bev, yaw_deg, fov_deg, intrinsics, ...}``.
- ``vlm.ask_with_thinking`` — base-model fallback for ambiguous / crowded counts.

No dense point cloud or segmentation mask is used. Cross-frame deduplication matches
instances by label plus nearby BEV position (bearing + depth → world XZ), or by their
canonical id via ``recon.get_instance``. Public method names and the KEYS of returned
dicts are preserved so callers are unchanged. The VLM handle is injected via
``set_vlm_module`` (mirrors Reconstruct), so methods do not take a ``vlm`` argument.
"""

import math
from typing import Any, Dict, List, Optional

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class CountingExpert(CPUTool):
    """Per-frame + cross-frame object counting over the model-free instance scene."""

    TOOL_PROMPT_DESCRIPTION = """\
### tools.Count — Object Counting Expert (CPU, model-free)

Counts objects per-frame and deduplicated across frames, reasoning over the
instance-level reconstruction (`recon.get_instances(t)`) with a `vlm` fallback for
ambiguous / crowded scenes. Cross-frame dedup matches objects by label + nearby
bird's-eye-view position, so the same physical object seen in several frames is
counted once.

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `count` | `(recon, frame=None, label=None)` | `int` | Count instances in one frame (optionally filtered by label) |
| `count_by_label` | `(recon, frame=None)` | `dict` | Per-label breakdown in one frame, e.g. `{"chair": 2, "table": 1}` |
| `count_across_frames` | `(recon, frames=None, label=None, dup_threshold=0.5)` | `dict` | Unique objects across frames with BEV deduplication |

**Example — how many chairs are in the scene? (cross-frame, deduplicated)**
```python
recon = tools.Reconstruct.Reconstruct(InputImages[:16], targets=["chair"])
result = tools.Count.count_across_frames(recon, frames=recon.frame_indices, label="chair")
print(f"Unique chairs: {result['unique_count']}")
```

**Example — per-label counts in one frame**
```python
recon = tools.Reconstruct.Reconstruct(InputImages[:1])
print(tools.Count.count_by_label(recon, frame=recon.frame_indices[0]))
```

**Example — ambiguous / crowded count, ask the base model directly**
```python
print(tools.Count.count(recon, frame=0, label="chair"))
# If instances look undercounted, cross-check with the base model:
# vlm.ask_with_thinking(InputImages[0], "How many chairs are visible? Reply with a number.")
```
"""

    DEFAULT_DUP_THRESHOLD_M = 0.5

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
        """Horizontal bearing of an instance from camera forward, degrees (+right)."""
        px = float(inst.get("point_1000", [500.0, 500.0])[0])
        frac = px / 1000.0 - 0.5
        return frac * CountingExpert._fov_deg(cam)

    @staticmethod
    def _inst_bev_xz(inst: dict, cam: Optional[dict]) -> tuple:
        """World BEV (x, z) meters of an instance, using the camera pose.

        Ego frame: z = forward = depth*cos(bearing), x = right = depth*sin(bearing).
        Rotated by camera yaw and translated by camera ``pos_bev`` into world XZ.
        """
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        ang = math.radians(CountingExpert._inst_angle_deg(inst, cam))
        ego_x = depth * math.sin(ang)  # right
        ego_z = depth * math.cos(ang)  # forward
        if not cam:
            return (ego_x, ego_z)
        yaw = math.radians(float(cam.get("yaw_deg", 0.0)))
        px, pz = cam.get("pos_bev", (0.0, 0.0))
        world_x = float(px) + ego_z * math.sin(yaw) + ego_x * math.cos(yaw)
        world_z = float(pz) - ego_z * math.cos(yaw) + ego_x * math.sin(yaw)
        return (world_x, world_z)

    # ------------------------------------------------------------------
    # Frame / label resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_frame(recon, frame: Optional[int]) -> int:
        if frame is not None:
            return int(frame)
        fis = getattr(recon, "frame_indices", None) or list((recon.instances or {}).keys())
        return int(fis[0]) if fis else 0

    @staticmethod
    def _base_label(lbl: str) -> str:
        """Strip a trailing numeric suffix, e.g. 'chair_0' -> 'chair'."""
        s = str(lbl)
        if "_" in s:
            head, tail = s.rsplit("_", 1)
            if tail.isdigit():
                return head
        return s

    @classmethod
    def _label_matches(cls, inst_label: str, target: Optional[str]) -> bool:
        if target is None:
            return True
        t = str(target).strip().lower()
        lab = str(inst_label).lower()
        base = cls._base_label(inst_label).lower()
        return t == lab or t == base or t in lab or lab in t

    @staticmethod
    def _frame_image(recon, frame: int):
        """Best-effort PIL image for a frame (for VLM fallbacks)."""
        fis = list(getattr(recon, "frame_indices", []) or [])
        stored = getattr(recon, "_input_images", None)
        if stored:
            if frame in fis and fis.index(frame) < len(stored):
                return stored[fis.index(frame)]
            return stored[0]
        return None

    # ------------------------------------------------------------------
    # Per-frame counting
    # ------------------------------------------------------------------

    def count(self, recon, frame: Optional[int] = None, label: Optional[str] = None) -> int:
        """Count instances in a single frame (no cross-frame dedup).

        Args:
            recon: Reconstruction object
            frame: frame index (defaults to the first frame)
            label: if provided, only count instances matching this label
        """
        frame = self._resolve_frame(recon, frame)
        insts = recon.get_instances(frame) or []
        return sum(1 for it in insts if self._label_matches(it.get("label", ""), label))

    def count_by_label(self, recon, frame: Optional[int] = None) -> Dict[str, int]:
        """Count instances grouped by base label (strips numeric ``_0`` suffixes)."""
        frame = self._resolve_frame(recon, frame)
        insts = recon.get_instances(frame) or []
        counts: Dict[str, int] = {}
        for it in insts:
            base = self._base_label(it.get("label", "object"))
            counts[base] = counts.get(base, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Cross-frame deduplicated counting
    # ------------------------------------------------------------------

    def count_across_frames(
        self,
        recon,
        frames: Optional[List[int]] = None,
        label: Optional[str] = None,
        dup_threshold: float = DEFAULT_DUP_THRESHOLD_M,
        use_optimal: bool = False,
        scene_memory=None,
    ) -> Dict[str, Any]:
        """Count unique objects across frames using BEV deduplication.

        Each instance is placed on the bird's-eye-view plane (bearing + ``depth_m``
        via ``_inst_bev_xz``). Same-label instances whose world XZ positions are
        within ``dup_threshold`` meters (across frames) are treated as one physical
        object. Instances sharing a canonical id (``recon.get_instance``) are always
        merged first.

        Args:
            recon: Reconstruction object
            frames: absolute frame indices to consider (defaults to all)
            label: if given, only count instances matching this label
            dup_threshold: BEV distance threshold for dedup (meters)
            use_optimal: use hierarchical clustering for the merge
            scene_memory: optional SceneMemory — registered with each unique entity

        Returns:
            dict with ``unique_count``, ``per_frame_counts``, ``centroids``.
        """
        if frames is None:
            frames = list(getattr(recon, "frame_indices", []) or sorted((recon.instances or {}).keys()))

        entries: List[Dict[str, Any]] = []
        per_frame: Dict[int, int] = {}

        for fi in frames:
            fi = int(fi)
            cam = recon.get_camera(fi)
            insts = recon.get_instances(fi) or []
            frame_count = 0
            for oi, it in enumerate(insts):
                if not self._label_matches(it.get("label", ""), label):
                    continue
                x, z = self._inst_bev_xz(it, cam)
                entries.append(
                    {
                        "frame": fi,
                        "object_idx": oi,
                        "label": self._base_label(it.get("label", "object")),
                        "canonical_id": it.get("id"),
                        "centroid": np.array([float(x), float(z)]),
                    }
                )
                frame_count += 1
            per_frame[fi] = frame_count

        if not entries:
            return {"unique_count": 0, "per_frame_counts": per_frame, "centroids": []}

        # Merge instances that share a canonical id across frames first.
        entries = self._merge_by_canonical_id(recon, entries)

        unique = self._deduplicate_centroids(entries, dup_threshold, use_optimal=use_optimal)

        if scene_memory is not None:
            for u in unique:
                try:
                    c = u["centroid"]
                    scene_memory.register_entity(
                        u["label"],
                        frame=u["frames_seen"][0],
                        centroid_3d=np.array([c[0], 0.0, c[1]]),
                    )
                except Exception:  # noqa: BLE001
                    pass

        return {
            "unique_count": len(unique),
            "per_frame_counts": per_frame,
            "centroids": [
                {
                    "label": u["label"],
                    "centroid": u["centroid"].tolist(),
                    "frames_seen": u["frames_seen"],
                }
                for u in unique
            ],
        }

    # ------------------------------------------------------------------
    # Dedup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_by_canonical_id(recon, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse entries that ``recon.get_instance`` reports as one track.

        A canonical id whose ``recon.get_instance(id)`` maps to multiple frames is a
        tracked object; all its per-frame entries are merged into one pre-cluster
        centroid so BEV dedup below cannot double-count a confidently tracked object.
        """
        get_instance = getattr(recon, "get_instance", None)
        if not callable(get_instance):
            return entries

        # canonical id -> list of entry indices carrying it
        by_id: Dict[str, List[int]] = {}
        for idx, e in enumerate(entries):
            cid = e.get("canonical_id")
            if cid is not None:
                by_id.setdefault(str(cid), []).append(idx)

        merged_idx = set()
        out: List[Dict[str, Any]] = []
        for cid, idxs in by_id.items():
            if len(idxs) < 2:
                continue
            try:
                track = get_instance(cid)
            except Exception:  # noqa: BLE001
                track = None
            if not isinstance(track, dict) or len(track) < 2:
                continue
            group = [entries[i] for i in idxs]
            centroid = np.mean([g["centroid"] for g in group], axis=0)
            frames_seen = sorted({g["frame"] for g in group})
            out.append(
                {
                    "frame": frames_seen[0],
                    "label": group[0]["label"],
                    "canonical_id": cid,
                    "centroid": centroid,
                    "_frames_seen": frames_seen,
                }
            )
            merged_idx.update(idxs)

        for idx, e in enumerate(entries):
            if idx not in merged_idx:
                out.append(e)
        return out

    @classmethod
    def _deduplicate_centroids(
        cls, entries: List[Dict[str, Any]], threshold: float, use_optimal: bool = False
    ) -> List[Dict[str, Any]]:
        """Greedily merge same-label entries whose BEV centroids are within threshold."""
        if use_optimal and len(entries) > 1:
            return cls._optimal_dedup(entries, threshold)

        unique: List[Dict[str, Any]] = []
        for entry in entries:
            entry_frames = entry.get("_frames_seen") or [entry["frame"]]
            merged = False
            for u in unique:
                if u["label"] != entry["label"]:
                    continue
                dist = float(np.linalg.norm(entry["centroid"] - u["centroid"]))
                if dist < threshold:
                    u["centroid"] = (u["centroid"] + entry["centroid"]) / 2.0
                    for f in entry_frames:
                        if f not in u["frames_seen"]:
                            u["frames_seen"].append(f)
                    merged = True
                    break
            if not merged:
                unique.append(
                    {
                        "label": entry["label"],
                        "centroid": entry["centroid"].copy(),
                        "frames_seen": sorted(set(entry_frames)),
                    }
                )
        for u in unique:
            u["frames_seen"] = sorted(set(u["frames_seen"]))
        return unique

    @classmethod
    def _optimal_dedup(cls, entries: List[Dict[str, Any]], threshold: float) -> List[Dict[str, Any]]:
        """Deduplicate per-label using hierarchical clustering for optimal assignment."""
        # Cluster within each label separately (never merge across labels).
        by_label: Dict[str, List[Dict[str, Any]]] = {}
        for e in entries:
            by_label.setdefault(e["label"], []).append(e)

        unique: List[Dict[str, Any]] = []
        for label, group in by_label.items():
            centroids = np.array([e["centroid"] for e in group])
            n = len(centroids)
            if n == 1:
                labels = [1]
            else:
                try:
                    from scipy.cluster.hierarchy import fcluster, linkage
                    from scipy.spatial.distance import pdist

                    Z = linkage(pdist(centroids), method="average")
                    labels = fcluster(Z, t=threshold, criterion="distance")
                except ImportError:
                    labels = list(range(1, n + 1))
                    for i in range(n):
                        for j in range(i + 1, n):
                            if labels[j] != labels[i]:
                                dist = float(np.linalg.norm(centroids[i] - centroids[j]))
                                if dist < threshold:
                                    old = labels[j]
                                    for k in range(n):
                                        if labels[k] == old:
                                            labels[k] = labels[i]

            groups: Dict[int, List[int]] = {}
            for idx, lab in enumerate(labels):
                groups.setdefault(int(lab), []).append(idx)

            for indices in groups.values():
                mean_c = np.mean([group[i]["centroid"] for i in indices], axis=0)
                frames_seen: List[int] = []
                for i in indices:
                    for f in group[i].get("_frames_seen") or [group[i]["frame"]]:
                        if f not in frames_seen:
                            frames_seen.append(f)
                unique.append(
                    {
                        "label": label,
                        "centroid": mean_c,
                        "frames_seen": sorted(set(frames_seen)),
                    }
                )
        return unique

    def __repr__(self) -> str:
        return "CountingExpert(methods: count, count_by_label, count_across_frames)"
