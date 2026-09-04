"""Orientation expert: estimate object facing direction via VLM + instance geometry."""

from typing import Any, Dict, Optional, Union

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class OrientationExpert(CPUTool):
    """Estimate object facing direction (model-free / prompt mode).

    Facing is read semantically from the frame image with a ``vlm.ask`` call and
    combined with lightweight per-instance geometry (bbox_1000 / point_1000 /
    depth_m + ``recon.get_camera``). ``facing_in_image`` is a whole-image,
    self-consistency-voted VLM judgment and needs no reconstruction at all.
    """

    TOOL_PROMPT_DESCRIPTION = """\
### tools.Orientation — Object Orientation Expert (CPU)

Estimates object facing direction. Two modes:
- `facing_in_image` — **whole-image single-pass VLM judgment**. Needs no
  reconstruction. **Prefer this for single-glance orientation/facing questions**
  (e.g. "which way is the giraffe facing?"), and especially in prompt/model-free
  mode.
- `facing_direction` / `principal_axes` / `compare_orientations` — derive a
  camera-relative facing label from `recon.get_instances` (bbox_1000, point_1000,
  depth_m, `recon.get_camera`) plus a `vlm.ask` read; use when you already have
  reconstructed instances.

`facing_in_image`, `principal_axes` and `compare_orientations` may be called
directly on `tools.Orientation`.

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `facing_in_image` | `(image, target, vlm, options=None, perspective="camera", votes=3)` | `dict` | Holistic, terse, **majority-voted** facing judgment (no recon). `perspective="egocentric"` for "imagine you're X" questions. |
| `facing_direction` | `(recon, frame, obj)` | `dict` | Estimate which direction the object is facing (instance + `vlm.ask`) |
| `principal_axes` | `(recon, frame, obj)` | `dict` | Bbox-based geometric descriptor of the instance (image-plane axis + elongation) |
| `compare_orientations` | `(recon, frame, obj_a, obj_b)` | `dict` | Compare facing directions of two objects |

**`facing_in_image` — preferred for facing/orientation questions**
```python
# Pass the FULL image (never a tight crop). Pass the MC options for letter voting.
opts = ["A. front-left", "B. right", "C. left", "D. back-left"]

# "imagine you're the giraffe — which way are you facing?" → egocentric
res = tools.Orientation.facing_in_image(image, "the giraffe", vlm,
                                        options=opts, perspective="egocentric")
# plain "which way does the giraffe face?" → camera (default)
# res = tools.Orientation.facing_in_image(image, "the giraffe", vlm, options=opts)

ReturnAnswer(res["answer"])   # <-- commit the voted letter IMMEDIATELY
```
`res["answer"]` is the majority-voted option letter; `res["confidence"]`/
`res["vote_counts"]` show agreement. **Do NOT** follow this with extra cropped or
leading `vlm.ask` calls — that dilutes the vote and reintroduces bias. This skill
already votes over several independent reworded reads and returns a terse letter
(matching the model's gut, which beats verbose step-by-step reasoning here).

For "imagine you are X / from X's perspective" orientation questions, use
`perspective="egocentric"` — it reframes left/right/front/back into X's own frame.

**`facing_direction` output**:
```python
{"facing": "right",           # camera-relative direction label
 "facing_vector": [x, y, z],  # unit vector (+x=right, -z=forward, y=up)
 "confidence": "high",        # "high" if the VLM gave a clear direction
 "elongation_ratio": 2.3,     # bbox aspect ratio (long side / short side)
 "obj": "person"}
```

**Note on ambiguity**: when `confidence` is `"low"` the facing read was unclear;
use `vlm.ask_with_thinking()` on the frame image for semantic disambiguation.

**Example**
```python
ori = tools.Orientation.facing_direction(recon, frame=fi, obj="person")
print(f"The person is facing {ori['facing']}")
if ori["confidence"] == "low":
    # Unclear read — ask VLM directly for disambiguation
    img = recon._input_images[recon.frame_indices.index(fi)]
    answer = vlm.ask_with_thinking(img, "Which direction is this object facing?")
```
"""

    _DIRECTION_MAP = {
        (1, 0): "right",
        (-1, 0): "left",
        (0, -1): "forward",
        (0, 1): "behind",
    }

    # camera-relative axis convention: +x = right, -z = forward, +y = up
    _DIRECTION_VECTORS = {
        "right": (1.0, 0.0, 0.0),
        "left": (-1.0, 0.0, 0.0),
        "forward": (0.0, 0.0, -1.0),
        "behind": (0.0, 0.0, 1.0),
    }

    def __init__(self):
        self._vlm_module = None
        self._tracer = None

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    # Instance / image helpers (model-free — instances + frame images only)
    # ------------------------------------------------------------------

    @staticmethod
    def _frame_image(recon, frame: int):
        """Best-effort PIL image for ``frame`` from ``recon._input_images``."""
        fis = list(getattr(recon, "frame_indices", []) or [])
        stored = getattr(recon, "_input_images", None)
        if not stored:
            return None
        if frame in fis and fis.index(frame) < len(stored):
            return stored[fis.index(frame)]
        return stored[0]

    @staticmethod
    def _find_instance(insts, obj: Union[int, str]) -> Optional[dict]:
        """Locate an instance by index, canonical id, or label substring."""
        if isinstance(obj, int):
            return insts[obj] if 0 <= obj < len(insts) else None
        t = str(obj).strip().lower()
        for it in insts:
            lab = str(it.get("label", "")).lower()
            iid = str(it.get("id", "")).lower()
            if t == lab or t in lab or lab in t or t in iid:
                return it
        return None

    @staticmethod
    def _resolve_label(recon, frame: int, obj: Union[int, str]) -> str:
        if isinstance(obj, str):
            return obj
        inst = OrientationExpert._find_instance(recon.get_instances(frame), obj)
        if inst is not None:
            return str(inst.get("label", f"object_{obj}"))
        return f"object_{obj}"

    @staticmethod
    def _bbox_elongation(inst: dict) -> float:
        """Long-side / short-side aspect ratio of the instance bbox."""
        bbox = inst.get("bbox_1000") or inst.get("bbox_pixel")
        if not bbox or len(bbox) < 4:
            return 1.0
        w = abs(float(bbox[2]) - float(bbox[0]))
        h = abs(float(bbox[3]) - float(bbox[1]))
        lo = min(w, h)
        if lo < 1e-6:
            return 1.0
        return round(max(w, h) / lo, 2)

    @staticmethod
    def _instance_centroid_xyz(inst: dict, cam: Optional[dict]):
        """Camera-frame (x_right, y_up, z_forward) meters from point_1000 + depth_m."""
        fov = float(cam.get("fov_deg") or 60.0) if cam else 60.0
        px = float((inst.get("point_1000") or [500.0, 500.0])[0])
        frac = px / 1000.0 - 0.5
        bearing = np.radians(frac * fov)
        dist = float(inst.get("depth_m", 0.0) or 0.0)
        ego_x = dist * float(np.sin(bearing))
        ego_z = dist * float(np.cos(bearing))
        return [ego_x, 0.0, ego_z]

    @staticmethod
    def principal_axes(recon, frame: int, obj: Union[int, str]) -> Dict[str, Any]:
        """Lightweight geometric descriptor of an instance (model-free).

        In model-free mode this reports the instance's image-plane principal
        axis (from its bbox aspect ratio) and a camera-frame centroid
        (from point_1000 + depth_m + ``recon.get_camera``). Keeps the historic
        keys (``axes``, ``eigenvalues``, ``elongation_ratio``, ``centroid``,
        ``num_points``) so downstream callers stay unchanged.
        """
        insts = recon.get_instances(frame)
        inst = OrientationExpert._find_instance(insts, obj)
        if inst is None:
            label = OrientationExpert._resolve_label(recon, frame, obj)
            raise ValueError(f"No instance for '{label}' at frame {frame}.")

        bbox = inst.get("bbox_1000") or inst.get("bbox_pixel") or [0, 0, 1, 1]
        w = abs(float(bbox[2]) - float(bbox[0])) or 1e-6
        h = abs(float(bbox[3]) - float(bbox[1])) or 1e-6
        ratio = OrientationExpert._bbox_elongation(inst)

        # Primary image-plane axis: horizontal if wider than tall, else vertical.
        if w >= h:
            primary = [1.0, 0.0, 0.0]
            secondary = [0.0, 1.0, 0.0]
        else:
            primary = [0.0, 1.0, 0.0]
            secondary = [1.0, 0.0, 0.0]
        axes = [primary, secondary, [0.0, 0.0, 1.0]]

        cam = recon.get_camera(frame)
        centroid = OrientationExpert._instance_centroid_xyz(inst, cam)

        return {
            "axes": axes,
            "eigenvalues": [round(max(w, h) ** 2, 2), round(min(w, h) ** 2, 2), 0.0],
            "elongation_ratio": ratio,
            "centroid": centroid,
            "num_points": 1,
        }

    @staticmethod
    def _parse_facing(text: str) -> str:
        """Map free-text VLM output to a camera-relative facing label."""
        t = (text or "").strip().lower()
        if not t:
            return "indeterminate"
        horiz = None
        if "left" in t:
            horiz = "left"
        elif "right" in t:
            horiz = "right"
        depth = None
        if (
            "toward" in t
            or "towards" in t
            or "forward" in t
            or "at the camera" in t
            or "at camera" in t
            or "front" in t
        ):
            depth = "forward"
        elif "away" in t or "behind" in t or "back" in t:
            depth = "behind"
        parts = [p for p in (depth, horiz) if p]
        if not parts:
            return "indeterminate"
        return "-".join(parts)

    @staticmethod
    def _direction_to_vector(label: str):
        v = np.zeros(3, dtype=np.float64)
        for part in str(label).split("-"):
            comp = OrientationExpert._DIRECTION_VECTORS.get(part)
            if comp is not None:
                v += np.array(comp, dtype=np.float64)
        norm = np.linalg.norm(v)
        if norm > 1e-6:
            v = v / norm
        return v.tolist()

    def facing_direction(self, recon, frame: int, obj: Union[int, str]) -> Dict[str, Any]:
        """Estimate which camera-relative direction an object is facing.

        Model-free: reads the facing semantically from the frame image with a
        ``vlm.ask`` call, maps it to a unit vector (+x=right, -z=forward), and
        reports the instance bbox elongation. When ``confidence`` is ``"low"``,
        use ``vlm.ask_with_thinking()`` for disambiguation.
        """
        insts = recon.get_instances(frame)
        inst = OrientationExpert._find_instance(insts, obj)
        label = OrientationExpert._resolve_label(recon, frame, obj)

        ratio = OrientationExpert._bbox_elongation(inst) if inst else 1.0

        direction = "indeterminate"
        img = OrientationExpert._frame_image(recon, frame)
        vlm = getattr(self, "_vlm_module", None)
        if img is not None and vlm is not None:
            ans = vlm.ask(
                img,
                f"Which direction is {label} facing relative to the camera/viewer? "
                f"Answer with one of: left, right, forward (toward the camera), "
                f"behind (away from the camera), or a short combination "
                f"(e.g. 'forward-left'). No explanation.",
            )
            direction = OrientationExpert._parse_facing(str(ans))

        confidence = "low" if direction == "indeterminate" else "high"
        facing_vec = OrientationExpert._direction_to_vector(direction)

        return {
            "facing": direction,
            "facing_vector": facing_vec,
            "confidence": confidence,
            "elongation_ratio": ratio,
            "obj": label,
        }

    def compare_orientations(self, recon, frame: int, obj_a: Union[int, str], obj_b: Union[int, str]) -> Dict[str, Any]:
        """Compare facing directions of two objects.

        Returns each object's facing direction and the angle between them.
        """
        fa = self.facing_direction(recon, frame, obj_a)
        fb = self.facing_direction(recon, frame, obj_b)

        va = np.array(fa["facing_vector"])
        vb = np.array(fb["facing_vector"])
        dot = float(np.clip(np.dot(va, vb), -1.0, 1.0))
        angle_deg = float(np.degrees(np.arccos(abs(dot))))

        if dot > 0.7:
            alignment = "same direction"
        elif dot < -0.7:
            alignment = "opposite directions"
        elif abs(dot) < 0.3:
            alignment = "perpendicular"
        else:
            alignment = f"{round(angle_deg)}° apart"

        return {
            "obj_a": fa["obj"],
            "obj_a_facing": fa["facing"],
            "obj_b": fb["obj"],
            "obj_b_facing": fb["facing"],
            "angle_deg": round(angle_deg, 1),
            "alignment": alignment,
        }

    # Neutral, reworded prompts for self-consistency voting. Each is a fresh,
    # independent VLM session — rewording (not temperature) gives the diversity.
    _FACING_REWORDS = [
        "Which direction is {t} facing?",
        "In this image, what direction is {t} oriented toward?",
        "Look at {t}. Which way is it turned / pointing?",
    ]

    @staticmethod
    def _extract_choice(text: str, options) -> str:
        """Pull a single option letter (A-H) from a VLM answer.

        Prefers the LAST standalone letter (camera ballots put a brief pose phrase
        first and the letter on the final line); falls back to option-body match.
        """
        import re

        if not text:
            return ""
        letters = re.findall(r"\b([A-H])\b", text.strip())
        if letters:
            return letters[-1].upper()
        # fall back: match option body text
        if isinstance(options, (list, tuple)):
            low = text.lower()
            for i, o in enumerate(options):
                body = re.sub(r"^\s*[A-H][\.\)]\s*", "", str(o)).strip().lower()
                if body and body in low:
                    return chr(65 + i)
        return ""

    @staticmethod
    def facing_in_image(
        image,
        target: str,
        vlm,
        options: Optional[Any] = None,
        perspective: str = "camera",
        votes: int = 1,
        think: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Judge which direction an object is facing, holistically on the FULL image.

        Model-free / prompt-mode friendly: needs NO reconstruction. Designed to
        match the base model's *gut* read (which beats verbose step-by-step
        reasoning on single-glance orientation questions):

        - **Terse, letter-only** answers (no chain-of-thought verbalization, which
          tends to drift the correct first impression).
        - **Self-consistency voting**: asks the same neutral question ``votes`` times,
          each reworded (independent sessions), and returns the MAJORITY letter.
        - **Perspective-conditional channel** (general principle: direct perceptual
          read → non-thinking; frame transform → thinking): ``perspective="camera"``
          (facing read in the current view) defaults to the fast NON-thinking
          channel — deliberation tends to drift a correct perceptual read;
          ``perspective="egocentric"`` ("imagine you're X") defaults to THINKING and
          adds a reframe step (read the facing, then transform left/right/front/back
          into X's own frame). Pass ``think=`` to override.

        After calling this, **ReturnAnswer the returned ``answer`` letter directly** —
        do NOT add extra cropped or leading follow-up ``vlm.ask`` calls (they dilute
        the vote and reintroduce the bias this skill removes).

        Args:
            image: the FULL image (PIL). Never a tight crop — body pose + scene
                context are what disambiguate facing.
            target: the object, e.g. "the giraffe", "the boy".
            vlm: the vlm_module (``vlm``) in the agent kernel.
            options: the multiple-choice options (list/str). Required for letter
                voting; without it, returns raw text.
            perspective: "camera" (default) or "egocentric" (imagine-you-are-X).
            votes: number of reworded independent asks to majority-vote over.

        Returns:
            {"target", "answer": <majority letter or raw text>, "perspective",
             "vote_counts": {letter: n}, "confidence": <frac>, "ballots": [...]}
        """
        from collections import Counter

        # Policy: intuition/facing answers use the NON-thinking channel by default
        # (deliberation drifts a correct gut read; nothink net-better on gemini-3.5).
        # Pass think=True to force the thinking channel for a specific hard case.
        if think is None:
            think = False
        _think = vlm.ask_with_thinking
        _nothink = getattr(vlm, "ask", None) or vlm.ask_with_thinking
        _read = _think if think else _nothink

        if isinstance(options, (list, tuple)):
            opt_lines = "\n".join(str(o) for o in options)
        elif options:
            opt_lines = str(options)
        else:
            opt_lines = ""
        # Camera-frame reads keep a BRIEF holistic read before the letter — pure
        # letter-only was too lossy for fine-grained directional bins (left/front/
        # front-right/…). Egocentric reads stay terse (the reframe already carries
        # the reasoning). Both still majority-vote across reworded ballots.
        if opt_lines:
            if perspective == "egocentric":
                opt_block = (
                    f"\nOptions:\n{opt_lines}\nAnswer with ONLY the single option letter (e.g. B). No explanation."
                )
            else:
                opt_block = (
                    f"\nOptions:\n{opt_lines}\n"
                    "First note the object's body pose in ONE short phrase, "
                    "then on a NEW LINE give ONLY the single option letter "
                    "(e.g. B) as the final line."
                )
        else:
            opt_block = "\nAnswer in 1-3 words. No explanation."

        if perspective == "egocentric":
            # Step 1: camera-frame facing read — a viewer-frame gut judgment, so
            # use the fast non-thinking channel. Step 2 (the reframe) will think.
            cam = _nothink(
                image,
                f"Look at the whole image. Which way is {target} facing relative to "
                f"the camera/viewer (toward camera / away / to our left / to our "
                f"right, or a combination)? Answer in a few words, no explanation.",
            )
            reframe = (
                f"Imagine you ARE {target}, standing in its body with its pose. "
                f'An observer notes: "{str(cam).strip()}". '
                f"Using YOUR OWN egocentric frame (your front = where your face/"
                f"body points; your left and right are as you'd experience them), "
                f"which option describes the direction you are facing?"
            )
            prompts = [reframe] * max(1, votes)
        else:
            base = (
                "Look at the whole image and judge from {t}'s overall body pose "
                "and the scene, not a single feature. {q}"
            )
            prompts = [
                base.format(
                    t=target,
                    q=OrientationExpert._FACING_REWORDS[i % len(OrientationExpert._FACING_REWORDS)].format(t=target),
                )
                for i in range(max(1, votes))
            ]

        # Egocentric ballots carry the logical left/right reframe → always think.
        _ballot = _read  # respects `think` (nothink by default; think=True forces thinking)
        ballots = []
        for p in prompts:
            ans = _ballot(image, p + opt_block)
            ballots.append(str(ans).strip())

        if not opt_lines:
            return {"target": target, "answer": ballots[0], "perspective": perspective, "ballots": ballots}

        letters = [OrientationExpert._extract_choice(b, options) for b in ballots]
        letters = [x for x in letters if x]
        counts = Counter(letters)
        if counts:
            winner, n = counts.most_common(1)[0]
            conf = round(n / len(ballots), 2)
        else:
            winner, conf = "", 0.0
        return {
            "target": target,
            "answer": winner,
            "perspective": perspective,
            "vote_counts": dict(counts),
            "confidence": conf,
            "ballots": ballots,
        }

    def __repr__(self) -> str:
        return "OrientationExpert(methods: facing_in_image, facing_direction, principal_axes, compare_orientations)"
