"""Cross-view entity matching (model-free).

Matches / deduplicates the SAME physical object across frames or camera views
using ONLY the instance-level, model-free reconstruction plus the base VLM:

- ``recon.get_instances(t)`` — per-frame ``{id, label, bbox_1000, bbox_pixel,
  point_1000, depth_m, conf, frame_idx}``.
- ``recon.get_camera(t)`` / ``recon.get_instance(canonical_id)`` — pose + tracks.
- ``vlm.ask_with_thinking`` — hard same-object identity calls on two frame crops.

Identity is decided from three signals: (a) canonical instance id (an id that the
reconstruction carries across frames), (b) label agreement, and (c) bird's-eye-view
(BEV) position proximity, where each instance's world position is derived from its
horizontal bearing (bbox center + camera FOV) and its ``depth_m`` distance in
meters, rotated/translated by the camera pose. No dense cloud, segmentation mask,
or per-object 3D centroid array is used.

Public method names (``match_across_views``, ``deduplicate``) and the KEYS of the
returned dicts are preserved so callers are unchanged. The VLM handle is injected
via ``set_vlm_module``; methods therefore do not take a ``vlm`` argument.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class EntityMatcher(CPUTool):
    """Cross-view entity matching over the model-free instance scene."""

    TOOL_PROMPT_DESCRIPTION = """\
### tools.Match — Cross-view Entity Matcher (CPU)

Decides whether the SAME physical object appears in more than one frame / camera
view, and collapses those sightings into one entity. Works entirely from the
reconstructed instances (`recon.get_instances`) plus the base model — it combines
the canonical instance id, the object label, and each instance's bird's-eye-view
(BEV) position (from its bbox-center bearing and `depth_m` distance in meters).
For genuinely ambiguous identity calls it asks `vlm.ask_with_thinking` on the two
frame images.

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `match_across_views` | `(recon, frames=None, label=None, images=None, match_dist_m=0.6)` | `dict` | Group all sightings of an object across frames/views into unique entities |
| `deduplicate` | `(recon, frames=None, label=None, match_dist_m=0.6)` | `dict` | Count distinct physical objects (of an optional label) across frames |

**When to use Match**:
- Use it when an object appears from different camera angles or frames and a naive
  per-frame count would double-count it, or when several similar objects sit close
  together and you must decide which sightings are the same one.

**Example — group sightings of a chair across two views**
```python
result = tools.Match.match_across_views(recon, frames=[0, 10], label="chair",
                                        images=InputImages)
print(f"Unique chairs across views: {result['unique_count']}")
for pair in result['matched_pairs']:
    print(f"  frame {pair['entity_a']['frame']} <-> frame {pair['entity_b']['frame']}"
          f" (score: {pair['score']:.2f})")
```

**Example — count distinct people across the clip**
```python
result = tools.Match.deduplicate(recon, frames=recon.frame_indices, label="person")
print(f"Unique people: {result['unique_count']}")
for c in result['centroids']:
    print(c['label'], c['frames_seen'])
```

**Example — inspect one object's cross-frame track**
```python
track = recon.get_instance("chair_0")   # {frame_idx: instance}
```
"""

    DEFAULT_MATCH_DIST_M = 0.6

    def __init__(self):
        self._vlm_module = None
        self._tracer = None

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    # Geometry helpers: bearing + ego/world BEV from bbox center + depth_m.
    # ------------------------------------------------------------------

    @staticmethod
    def _fov_deg(cam: Optional[dict]) -> float:
        if cam and cam.get("fov_deg"):
            return float(cam["fov_deg"])
        return 60.0

    @staticmethod
    def _inst_angle_deg(inst: dict, cam: Optional[dict]) -> float:
        """Horizontal bearing (deg, +right) of an instance from camera forward."""
        px = float(inst.get("point_1000", [500.0, 500.0])[0])
        frac = px / 1000.0 - 0.5
        return frac * EntityMatcher._fov_deg(cam)

    @staticmethod
    def _inst_bev_xz(inst: dict, cam: Optional[dict]) -> Tuple[float, float]:
        """World BEV (x, z) meters of an instance, using the camera pose.

        Ego frame: z = forward = depth*cos(bearing), x = right = depth*sin(bearing);
        rotated by camera yaw and translated by ``pos_bev`` into world XZ.
        """
        depth = float(inst.get("depth_m", 0.0) or 0.0)
        ang = math.radians(EntityMatcher._inst_angle_deg(inst, cam))
        ego_x = depth * math.sin(ang)
        ego_z = depth * math.cos(ang)
        if not cam:
            return (ego_x, ego_z)
        yaw = math.radians(float(cam.get("yaw_deg", 0.0)))
        px, pz = cam.get("pos_bev", (0.0, 0.0))
        world_x = float(px) + ego_z * math.sin(yaw) + ego_x * math.cos(yaw)
        world_z = float(pz) - ego_z * math.cos(yaw) + ego_x * math.sin(yaw)
        return (world_x, world_z)

    # ------------------------------------------------------------------
    # Frame / label / image resolution.
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_frames(recon, frames) -> List[int]:
        if frames is not None:
            try:
                return [int(f) for f in frames]
            except TypeError:
                return [int(frames)]
        fis = getattr(recon, "frame_indices", None) or list((getattr(recon, "instances", {}) or {}).keys())
        return [int(f) for f in fis]

    @staticmethod
    def _base_label(label: str) -> str:
        """Strip a trailing ``_<idx>`` disambiguator and lowercase."""
        base = str(label)
        if "_" in base:
            head, tail = base.rsplit("_", 1)
            if tail.isdigit():
                base = head
        return base.lower().strip()

    @classmethod
    def _label_matches_filter(cls, obj_label: str, want: Optional[str]) -> bool:
        if want is None:
            return True
        base = cls._base_label(obj_label)
        w = str(want).lower().strip()
        return base == w or w in base or base in w

    @staticmethod
    def _frame_image(recon, images, frame: int):
        """Best-effort PIL image for a frame (for VLM identity calls)."""

        def _plain(x):
            return x.image if hasattr(x, "image") else x

        fis = list(getattr(recon, "frame_indices", []) or [])
        if images:
            imgs = list(images)
            if frame in fis:
                loc = fis.index(frame)
                if loc < len(imgs):
                    return _plain(imgs[loc])
            return _plain(imgs[0]) if imgs else None
        stored = getattr(recon, "_input_images", None)
        if stored:
            if frame in fis and fis.index(frame) < len(stored):
                return stored[fis.index(frame)]
            return stored[0]
        return None

    def _crop(self, recon, images, entity: Dict[str, Any]):
        """Crop the frame image to the entity's pixel bbox (falls back to full frame)."""
        img = self._frame_image(recon, images, entity["frame"])
        if img is None:
            return None
        box = entity.get("bbox_pixel")
        if box and len(box) == 4:
            try:
                x1, y1, x2, y2 = [int(round(float(v))) for v in box]
                if x2 > x1 and y2 > y1:
                    return img.crop((x1, y1, x2, y2))
            except Exception:  # noqa: BLE001
                pass
        return img

    # ------------------------------------------------------------------
    # Entity collection.
    # ------------------------------------------------------------------

    def _collect_entities(self, recon, frames: List[int], label: Optional[str]) -> List[Dict[str, Any]]:
        entities: List[Dict[str, Any]] = []
        for fi in frames:
            try:
                insts = recon.get_instances(fi) or []
            except Exception:  # noqa: BLE001
                insts = []
            cam = None
            try:
                cam = recon.get_camera(fi)
            except Exception:  # noqa: BLE001
                cam = None
            for oi, it in enumerate(insts):
                obj_label = str(it.get("label", f"object_{oi}"))
                if not self._label_matches_filter(obj_label, label):
                    continue
                bev = self._inst_bev_xz(it, cam)
                entities.append(
                    {
                        "frame": int(fi),
                        "object_idx": oi,
                        "label": obj_label,
                        "id": it.get("id"),
                        "bev": (float(bev[0]), float(bev[1])),
                        "depth_m": float(it.get("depth_m", 0.0) or 0.0),
                        "bbox_1000": it.get("bbox_1000"),
                        "bbox_pixel": it.get("bbox_pixel"),
                    }
                )
        return entities

    # ------------------------------------------------------------------
    # Pairwise identity + grouping (union-find).
    # ------------------------------------------------------------------

    def _same_entity(
        self, recon, images, a: Dict[str, Any], b: Dict[str, Any], match_dist_m: float, use_vlm: bool
    ) -> bool:
        # Same frame -> different sightings, never merge.
        if a["frame"] == b["frame"]:
            return False

        # (a) canonical id carried across frames.
        if a.get("id") and b.get("id") and a["id"] == b["id"]:
            return True

        # (b) labels must be compatible.
        la, lb = self._base_label(a["label"]), self._base_label(b["label"])
        if not (la == lb or la in lb or lb in la):
            return False

        # (c) BEV proximity.
        dist = math.hypot(a["bev"][0] - b["bev"][0], a["bev"][1] - b["bev"][1])
        if dist <= match_dist_m:
            return True

        # Ambiguous band -> optional VLM identity adjudication on frame crops.
        if use_vlm and self._vlm_module is not None and dist <= 2.0 * match_dist_m:
            ca, cb = self._crop(recon, images, a), self._crop(recon, images, b)
            if ca is not None and cb is not None:
                q = (
                    f"These two crops are from different frames of one video. "
                    f"Is the {la} in the first image the SAME physical object as the "
                    f"{lb} in the second image? Answer YES or NO and briefly why."
                )
                try:
                    ans = self._vlm_module.ask_with_thinking([ca, cb], q)
                    return "yes" in (ans or "").strip().lower()[:40]
                except Exception:  # noqa: BLE001
                    return False
        return False

    def _group(
        self, recon, images, entities: List[Dict[str, Any]], match_dist_m: float, use_vlm: bool
    ) -> List[List[Dict[str, Any]]]:
        n = len(entities)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)

        for i in range(n):
            for j in range(i + 1, n):
                if self._same_entity(recon, images, entities[i], entities[j], match_dist_m, use_vlm):
                    union(i, j)

        groups: Dict[int, List[Dict[str, Any]]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(entities[i])
        # Sort each group by frame for stable representatives.
        out = [sorted(g, key=lambda e: e["frame"]) for g in groups.values()]
        out.sort(key=lambda g: (g[0]["frame"], g[0]["object_idx"]))
        return out

    # ------------------------------------------------------------------
    # Public API.
    # ------------------------------------------------------------------

    def match_across_views(
        self,
        recon,
        frames: Optional[List[int]] = None,
        label: Optional[str] = None,
        images=None,
        match_dist_m: float = DEFAULT_MATCH_DIST_M,
        use_vlm: bool = True,
    ) -> Dict[str, Any]:
        """Group every sighting of an object across frames/views into unique entities.

        Scans ``recon.get_instances(f)`` over ``frames`` (all frames if None),
        optionally filtered by ``label``, derives each instance's BEV world
        position, then merges sightings that share a canonical id, agree on label,
        and sit within ``match_dist_m`` in BEV. Sightings in an ambiguous distance
        band are adjudicated by ``vlm.ask_with_thinking`` on the two frame crops.

        Returns:
            dict with keys ``unique_count``, ``matched_pairs``, ``unmatched``,
            ``entities`` (KEYS unchanged from the previous implementation).
        """
        frames = self._resolve_frames(recon, frames)
        entities = self._collect_entities(recon, frames, label)

        if len(entities) <= 1:
            return {
                "unique_count": len(entities),
                "matched_pairs": [],
                "unmatched": [_entity_summary(e) for e in entities],
                "entities": [_entity_summary(e) for e in entities],
            }

        groups = self._group(recon, images, entities, match_dist_m, use_vlm)

        matched_pairs = []
        for group in groups:
            if len(group) > 1:
                for i in range(len(group) - 1):
                    matched_pairs.append(
                        {
                            "entity_a": _entity_summary(group[i]),
                            "entity_b": _entity_summary(group[i + 1]),
                            "score": 1.0,
                        }
                    )

        return {
            "unique_count": len(groups),
            "matched_pairs": matched_pairs,
            "unmatched": [_entity_summary(g[0]) for g in groups if len(g) == 1],
            "entities": [_entity_summary(e) for e in entities],
        }

    def deduplicate(
        self,
        recon,
        frames: Optional[List[int]] = None,
        label: Optional[str] = None,
        match_dist_m: float = DEFAULT_MATCH_DIST_M,
        use_vlm: bool = False,
    ) -> Dict[str, Any]:
        """Count distinct physical objects across frames (BEV-proximity dedup).

        Same grouping as ``match_across_views`` but returns a count-oriented digest.
        ``use_vlm`` defaults to False here (pure geometry) to keep bulk counting fast.

        Returns:
            dict with keys ``unique_count``, ``centroids``, ``per_frame_counts``
            (KEYS unchanged). Each ``centroids`` entry carries the BEV ``[x, z]``
            position of the group representative instead of a dense 3D centroid.
        """
        frames = self._resolve_frames(recon, frames)
        entities = self._collect_entities(recon, frames, label)

        if not entities:
            return {"unique_count": 0, "centroids": [], "per_frame_counts": {}}

        groups = self._group(recon, images=None, entities=entities, match_dist_m=match_dist_m, use_vlm=use_vlm)

        per_frame: Dict[int, int] = {}
        for e in entities:
            per_frame[e["frame"]] = per_frame.get(e["frame"], 0) + 1

        return {
            "unique_count": len(groups),
            "centroids": [
                {
                    "label": g[0]["label"],
                    "centroid": [round(g[0]["bev"][0], 4), round(g[0]["bev"][1], 4)],
                    "frames_seen": sorted(set(e["frame"] for e in g)),
                }
                for g in groups
            ],
            "per_frame_counts": per_frame,
        }

    def __repr__(self) -> str:
        return "EntityMatcher(methods: match_across_views, deduplicate)"


def _entity_summary(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an entity dict to a serializable summary (KEYS preserved)."""
    return {
        "label": entity["label"],
        "frame": entity["frame"],
        "object_idx": entity["object_idx"],
        "id": entity.get("id"),
        "centroid": [round(float(entity["bev"][0]), 4), round(float(entity["bev"][1]), 4)],
    }
