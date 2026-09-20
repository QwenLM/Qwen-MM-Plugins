"""Shared prompt sections used by system_prompt.py and planning_prompt.py."""

import logging
from typing import Any, Dict, Set

_logger = logging.getLogger(__name__)


def resolve_section(name: str, default_content: str, ablations: dict) -> str:
    if not ablations:
        return default_content
    if name in ablations.get("exclude", []):
        _logger.info("[prompt ablation] EXCLUDED: %s", name)
        return ""
    override_path = ablations.get("override", {}).get(name)
    if override_path:
        _logger.info("[prompt ablation] OVERRIDDEN: %s -> %s", name, override_path)
        with open(override_path, "r") as f:
            return f.read().rstrip()
    return default_content


def warn_unknown_sections(ablations: dict, valid_sections: Set[str], prompt_name: str) -> None:
    if not ablations:
        return
    for name in ablations.get("exclude", []):
        if name not in valid_sections:
            _logger.warning(
                "[prompt ablation] Unknown section '%s' in exclude list for %s. Valid sections: %s",
                name,
                prompt_name,
                sorted(valid_sections),
            )
    for name in ablations.get("override", {}):
        if name not in valid_sections:
            _logger.warning(
                "[prompt ablation] Unknown section '%s' in override dict for %s. Valid sections: %s",
                name,
                prompt_name,
                sorted(valid_sections),
            )


def build_input_description(metadata: Dict[str, Any]) -> str:
    is_video = metadata.get("is_video", False)
    num_images = metadata.get("num_images", 0)
    fps = metadata.get("fps")
    total_frames = metadata.get("total_frames")
    duration = metadata.get("duration_sec")
    num_videos = metadata.get("num_videos", 1)
    videos = metadata.get("videos")

    if is_video:
        if num_videos > 1 and videos:
            desc = f"MULTI-VIDEO INPUT: {num_videos} videos, {num_images} total frames."
            parts = []
            for i, v in enumerate(videos):
                vname = v.get("name") or f"video_{i + 1}"
                vfps = v.get("fps")
                vframes = v.get("num_frames", 0)
                vdur = v.get("duration_sec")
                bits = [f"InputImages_{i + 1} ({vname})", f"{vframes} frames"]
                if vfps:
                    bits.append(f"{vfps} FPS")
                if vdur:
                    bits.append(f"{vdur:.1f}s")
                parts.append(": ".join([bits[0], ", ".join(bits[1:])]))
            desc += "\n  " + "\n  ".join(parts)
        else:
            if num_images == total_frames:
                desc = f"VIDEO: {num_images} frames at {fps} FPS."
            else:
                desc = f"VIDEO: {num_images} sampled frames from {total_frames} total at {fps} FPS."
            if duration:
                desc += f" Duration: {duration:.1f}s."
    else:
        desc = f"{num_images} static images."

    return desc


def vlm_api_section(**_kwargs) -> str:
    return """## Visual Access — `show`, `vlm.locate`, `vlm.ask`, `vlm.ask_with_thinking`

You have four ways to obtain visual information:

- `show(visual_input)` — display image(s) inline in the next feedback so you can see them yourself.
- `vlm.locate(visual_input, question)` → `str` — ask a grounding VLM for pixel coordinates of an object you describe. Up to 8 images per call.
- `vlm.ask(visual_input, question)` → `str` — **fast NON-thinking** query for single-glance judgments (object facing/orientation, appearance, gestalt). Answers directly from first impression, no step-by-step reasoning. Reply is a letter (if options given) or 1-3 words. Up to 64 images.
- `vlm.ask_with_thinking(visual_input, question)` → `str` — ask a separate visual reasoner that deliberates over the provided frames and returns a text answer. Up to 64 images per call.

### Choosing a tool

Each tool produces a particular shape of evidence:

- `vlm.locate`             — pixel coordinates of an object you can describe in words.
- `vlm.ask`                — a quick, gut perceptual judgment (facing/appearance) with no deliberation.
- `tools.Reconstruct`      — an instance-level scene: per-frame objects with a bounding box + estimated distance (meters) + a camera pose (`recon.instances[t]` / `recon.cameras[t]` / `recon.bev_visual()`).
- `tools.Geometry`         — numeric ops on coordinates and arrays.
- `show()`                 — you look at images yourself and decide.
- `vlm.ask_with_thinking`  — a visual reasoner returns a text answer about the provided frames.

Use a tool when its evidence shape matches what the question needs AND you expect it to produce a reliable result on this specific input. "Reliable" depends on the input, not just the question type — for example, `tools.Reconstruct` gives useful object positions when the base model can ground the objects and estimate their distance, and weaker output otherwise; `vlm.locate` needs an object describable in words.

**`vlm.ask` vs `vlm.ask_with_thinking`**: use `vlm.ask` for single-glance perceptual reads where deliberation would only drift a correct first impression (orientation, appearance). Use `vlm.ask_with_thinking` when the answer genuinely needs reasoning across frames, or when a specialized tool failed.

`vlm.ask_with_thinking` covers two cases:
  (a) the question needs a kind of evidence none of the specialized tools produces (e.g., semantic understanding, action recognition, object identity);
  (b) a specialized tool was tried and failed (empty mask, ambiguous output). Do NOT skip tools in favor of `vlm.ask_with_thinking` for spatial relationship questions — VLM spatial perception is unreliable without grounded coordinates.

`show()` is for judgments you want to make yourself by looking.

### Working with `vlm.locate`

`vlm.locate` returns coordinates in **labeled, self-describing form** — a point as
`(x=<v>, y=<v>)`, a box as `(x1=<v>, y1=<v>, x2=<v>, y2=<v>)` — where values are
**0-1000 normalized** (NOT pixels), origin top-left, `x`=horizontal, `y`=vertical.
The `x=`/`y=` labels tell you the axis order, so never guess whether it is x,y or y,x.
- Center point: `vlm.locate(image, "Give the center of <object> as (x=.., y=..) in 0-1000 normalized scale.")`
- Bounding box: `vlm.locate(image, "Give the bounding box of <object> as (x1=.., y1=.., x2=.., y2=..) in 0-1000 normalized scale.")`

Parse by label (robust to axis order); convert to pixels only for pixel ops:
```python
import re
d = dict(re.findall(r"(x1|y1|x2|y2|x|y)\\s*=\\s*([-+]?\\d*\\.?\\d+)", ans))
vlm_x, vlm_y = float(d["x"]), float(d["y"])            # 0-1000 normalized
px, py = tools.Geometry.normalized_to_pixel((vlm_x, vlm_y), W, H)
```
Feed `vlm.locate` output straight into `tools.Draw.mark` / `tools.Draw.match_view`
(they take 0-1000 coords by default). Estimating coordinates yourself by eye is unreliable.

`vlm.locate` may return the literal string `Not visible` (optionally followed by a short note) when the requested object/annotation is absent or ambiguous in the image. Always check for this before parsing the answer as coordinates; if you see it, try a different frame, refine the description, or first locate the target frame with `vlm.ask_with_thinking`.

### Working with `vlm.ask_with_thinking`

The session is independent: it sees only the images and the question you pass in. Do not write your prior conclusions, prior vlm answers, or expected answers into the question — phrase the question on its own terms so the answer is formed from the images.

Pick the frames the question is about (up to 64). Frames unrelated to the question add noise; include only the ones you want considered.

You can re-call the tool freely:
- if the answer is "Cannot determine from the images.", try a different frame selection or a different phrasing,
- to confirm an answer, call again with different frames or call `show()` and look yourself.

Multiple consistent answers across calls are stronger evidence than a single call. Inconsistent answers across calls mean the question is under-constrained — refine the framing or pick more informative frames.

### Pitfalls

- **NEVER** overwrite the `vlm` or `feedback` variables: `vlm = vlm.locate(...)` or `feedback = feedback.show(...)` destroys the module. Always assign to a different name: `coords = vlm.locate(...)`, `answer = vlm.ask_with_thinking(...)`."""


def return_answer_section() -> str:
    return """## ReturnAnswer — Submit Your Final Answer

```python
ReturnAnswer("B")                              # multiple-choice letter
ReturnAnswer(3)                                # integer
ReturnAnswer(3.14)                             # float
ReturnAnswer("The dog is left of the cat")     # free-form text
```

Accepts `str`, `int`, or `float`. Call once to submit your final answer and terminate."""


def coordinate_system_section() -> str:
    return """## Coordinate Systems — Resolving the Implicit Frame of Reference

Every spatial question implicitly assumes a coordinate system, but almost never says which one. Your first job on any spatial question is to **resolve this ambiguity** — the wrong coordinate system gives the wrong answer even with perfect computation.

### The Core Problem

There are four coordinate spaces, and they can disagree:

- **Pixel space** (2D image plane): Where things appear in the image. Pixel position is a projection — it conflates camera motion with object motion. An object sliding rightward in the image could mean the object moved right, or the camera panned left, or both. **Pixel-space observations about motion or spatial relationships are fundamentally ambiguous** and should never be used as evidence for real-world spatial claims.

- **Camera space** (3D relative to a camera at a specific frame): "Left," "right," "in front," "behind" as seen from a particular viewpoint. This frame changes every frame as the camera moves — "to the left" at frame 0 may be "behind" at frame 50. Any camera-relative claim requires specifying **which frame's camera**.

- **World space** (3D global): Fixed coordinates that don't move with the camera. Object positions in world space are consistent across time — this is where real distances, speeds, and trajectories live. But "world left" is defined by the reconstruction (anchored to the first camera), which is arbitrary — it has no inherent meaning like "north" or "east."


- **Object perspective** (relative to an object's facing direction): "To the car's left," "in front of the person." This is NOT camera-relative — an object facing the camera has its left/right **mirrored** vs. the camera's. First determine which way the object faces, then define left/right/front/behind from that heading.

### Technical Convention

- Perception is **instance-level** (model-free). `tools.Reconstruct.Reconstruct(frames, targets=[...])`
  gives, per frame `t`:
  - `recon.get_instances(t)` → list of `{id, label, bbox_1000:[x1,y1,x2,y2] (0-1000), point_1000:[cx,cy] (bbox centre, 0-1000), depth_m (distance from the camera, meters), conf}`.
  - `recon.get_camera(t)` → `{pos_bev:[x,z] (world meters), yaw_deg (+=turned right; forward is world -Z at yaw 0), fov_deg (~60), ...}`.
- **World frame**: **+Y** up, **-Z** = first camera's forward, **+X** = right from the first camera. World BEV is the `(x, z)` plane.

### Relative Direction Computation (LEFT/RIGHT/FRONT/BEHIND)

**ALWAYS use this quantitative method for direction questions. NEVER guess direction from 2D image appearance.**

An object's horizontal bearing from the camera comes from its bbox-centre x and the camera FOV;
its forward/lateral offset then follow from its distance:
```python
import math
fi = recon.frame_indices[0]                       # absolute frame index
insts = recon.get_instances(fi)
it = next(o for o in insts if o["label"] == "X")  # or match by id / substring
cam = recon.get_camera(fi)
fov = cam.get("fov_deg", 60.0)

bearing_deg = (it["point_1000"][0] / 1000.0 - 0.5) * fov   # + = right of image centre
dist_m = it["depth_m"]                                     # distance from camera (meters)
fwd_m   = dist_m * math.cos(math.radians(bearing_deg))     # + = FRONT (into the scene)
right_m = dist_m * math.sin(math.radians(bearing_deg))     # + = RIGHT, - = LEFT
```

### Virtual Viewpoint Computation (Pattern B)

For spatial imagination / embodied reasoning / proxy direction questions:
"Stand at A facing B, where is C?" — construct a virtual viewpoint at object A.

```python
import math, numpy as np
cam = recon.get_camera(fi)
fov = cam.get("fov_deg", 60.0)
yaw = math.radians(cam.get("yaw_deg", 0.0))
cpx, cpz = cam.get("pos_bev", (0.0, 0.0))

def bev_xz(label):                       # world BEV (x, z) of an instance by label
    o = next(it for it in recon.get_instances(fi) if it["label"] == label)
    b = math.radians((o["point_1000"][0] / 1000.0 - 0.5) * fov)
    ex, ez = o["depth_m"] * math.sin(b), o["depth_m"] * math.cos(b)   # ego right, forward
    wx = cpx + ez * math.sin(yaw) + ex * math.cos(yaw)
    wz = cpz - ez * math.cos(yaw) + ex * math.sin(yaw)
    return np.array([wx, wz])

A, B, C = bev_xz("A"), bev_xz("B"), bev_xz("C")
fwd = B - A; fwd = fwd / np.linalg.norm(fwd)     # A's facing = toward B (on the BEV plane)
right = np.array([fwd[1], -fwd[0]])              # 90° clockwise on the BEV plane
dot_f = np.dot(C - A, fwd)                        # positive = FRONT, negative = BEHIND
dot_r = np.dot(C - A, right)                      # positive = RIGHT, negative = LEFT
```

### Camera Motion Computation (Pattern C)

For camera translation / rotation between two frames, read the camera poses directly:

```python
import math
c1 = recon.get_camera(f1); c2 = recon.get_camera(f2)

# Translation on the world BEV plane
dx = c2["pos_bev"][0] - c1["pos_bev"][0]
dz = c2["pos_bev"][1] - c1["pos_bev"][1]

# Re-express the move in frame-1 camera axes
yaw1 = math.radians(c1["yaw_deg"])
moved_right   = dx * math.cos(yaw1) + dz * math.sin(yaw1)      # + = moved right
moved_forward = dx * math.sin(yaw1) - dz * math.cos(yaw1)      # + = moved forward

# Rotation between the two frames
turn_deg = ((c2["yaw_deg"] - c1["yaw_deg"] + 180) % 360) - 180  # + = turned right
```"""


def perception_strategy_section(**_kwargs) -> str:
    return """## Decision Protocol — walk it top-down, one small choice at a time

Do NOT pick a strategy from a flat menu. Answer ONE narrow question at each node,
then follow that branch to the next — one level at a time. Stop as soon as a leaf
gives you the answer.

**Q1 — Is the answer a single look, or does it need built evidence?**
  • A single look (which way something *faces*/*is oriented*, its appearance, a
    gestalt impression) → go to **Q2**.
  • It needs measured/derived evidence (distance, size, count, A-relative-to-B,
    camera motion, cross-frame/multi-view) → go to **Q4**.

**Q2 — Must the facing be re-expressed in another entity's frame?**
  • No — it is asked in the view you already have ("which way does X face" as you
    see it). This is a direct perceptual read: a single holistic look answers it,
    and deliberation tends to drift a correct perceptual judgment. →
    `tools.Orientation.facing_in_image(image, "X", vlm, options=OPTS)` →
    `ReturnAnswer(res["answer"])`. **STOP.**
  • Yes — "from X's point of view / imagine you are X": the left/right/front/back
    must be transformed into X's own frame, which is a reasoning step. →
    `tools.Orientation.facing_in_image(image, "X", vlm, options=OPTS,
    perspective="egocentric")` (reads the facing, then reasons the re-expression)
    → `ReturnAnswer(res["answer"])`. **STOP.**

**Q3 — Is it a pure appearance / colour / gestalt read (no orientation, no relation)?**
  • Yes → one `vlm.ask(image, "<neutral question>\\nOptions:...\\nAnswer with ONLY
    the letter")` (non-thinking) → `ReturnAnswer(...)`. **STOP.**
  (These plus Q2 are the only single-look leaves; anything relational, metric, or
  cross-frame belongs to Q4/Q5.)

  Rules for the single-look leaves (Q2–Q3) — a general principle, not tuned to any
  benchmark:
  - Use the FULL image — cropping discards the body pose / scene context that
    disambiguates facing.
  - Match the channel to the work: a **direct perceptual read → non-thinking
    `vlm.ask`** (deliberation drifts a correct gut read); a **frame transform /
    relation / derivation → thinking**. `facing_in_image` applies this for you.
  - Neutral phrasing — never embed your own directional guess in the question.
  - Once you have the leaf answer, ReturnAnswer immediately. Do NOT append extra
    cropped or leading follow-up calls (they dilute the answer).

**Q4 — Is one object property enough, or do you need a relation/geometry?**
  • One property (which frame contains X, is X present, what colour/state) →
    `vlm.locate` / `vlm.ask` / `show()`, verify, answer.
  • A relation or metric → go to **Q5**.

**Q5 — Build and cross-validate.**
  Ground the objects (`vlm.locate` → coordinates), derive geometry
  (`tools.Reconstruct` instances / `tools.Camera` / `tools.Geometry`), then combine in
  code. Require **≥2 independent lines of evidence** to agree before answering
  (see the Cross-Validation Principle). This is where multi-step decomposition
  earns its cost — reserve it for this branch only.

  **Strongly recommended for LEFT/RIGHT/FRONT/BEHIND and "from X's point of view"
  (virtual-viewpoint) questions**: (1) ground the objects; (2) VERIFY them on the frame
  first with `show(tools.Reconstruct.bbox_overlay(recon, frame=fi))` — confirm each
  reference object's box and `depth_m` are right, re-ground if not (a wrong bbox/depth
  is the top cause of a flipped answer); (3) `show(recon.bev_visual())` for the top-down
  layout; (4) compute the relation from the instances' BEV coordinates with
  `tools.Geometry` (Coordinate Systems Pattern A/B). The base model's raw left/right is
  unreliable — use `vlm.ask` / `vlm.ask_with_thinking` only to CROSS-CHECK the geometric
  result, not to decide it.
"""


def evidence_hierarchy_section(**_kwargs) -> str:
    return """## Cross-Validation Principle

No single evidence source is reliable alone. Every spatial conclusion must be supported
by at least two independent lines of evidence before you answer.

### Evidence Sources and Their Limitations
- **Visual perception** (`show()`): Good at object identity, appearance, scene semantics,
  qualitative layout. Unreliable for metric quantities, precise spatial relationships, and
  distinguishing real motion from apparent motion.
- **Geometric computation** (Reconstruct instances, code): Good at relative distances, angles,
  positions, quantitative comparisons. Only as reliable as its inputs — a mis-grounded object or
  a bad distance estimate propagates to wrong answers.
- **Visualizations** (BEV, plots): Good for sanity-checking and debugging.
  But visualizations are lossy representations — a BEV is a 2D projection of 3D data,
  and its appearance depends on reference frame, scale, and rendering choices. A visualization
  that "looks wrong" is not proof that the underlying data is wrong.
- **Logical reasoning** (code, math): Good at combining evidence and checking consistency.
  Only as reliable as its premises.

### When Sources Disagree — Diagnostic Protocol
Do NOT pick a side based on intuition. Every disagreement has a **root cause** — your job
is to find it by tracing each evidence chain back to its inputs:

1. **Identify the specific claim in conflict.** e.g., "Dot product says RIGHT, but BEV plot
   shows the object on the left side."
2. **Audit the computation chain.** For each step, print and verify:
   - Are the grounded instances present and on the correct objects? (`show(tools.Reconstruct.bbox_overlay(recon, frame=fi))`)
   - Are the derived coordinates non-NaN and physically plausible? (`print()` the values)
   - Is the correct frame index used? (camera pose at frame X, centroid from frame X)
   - Is the coordinate system correct? (camera-relative vs world-relative)
3. **Audit the visual evidence.** For each visual observation, ask:
   - Could the 2D appearance be misleading? (projection, foreshortening, reference frame)
   - Am I interpreting the visualization's axes and labels correctly?
   - Is this a qualitative impression or a precise measurement?
4. **Find the concrete error.** The disagreement must come from a specific, identifiable
   mistake — wrong instance, wrong frame, wrong coordinate system, misleading projection, etc.
   If you cannot identify a concrete error in either chain, you do not have enough information
   to override either conclusion. Gather more evidence instead.
5. **Never override evidence with intuition.** "It looks wrong" is not a diagnosis.
   You must point to the specific broken step before changing your answer."""


def show_api_section(
    max_show_images_per_step: int = -1,
    max_show_images_per_session: int = 250,
    num_videos: int = 1,
) -> str:
    budget_lines = []
    if max_show_images_per_session >= 0:
        budget_lines.append(
            f"- **show() budget: {max_show_images_per_session} total images** across "
            f"the entire session. Remaining budget is reported as `[show() budget]` "
            f"in feedback after each step that uses show()."
        )
    if max_show_images_per_step >= 0:
        budget_lines.append(
            f"- Max **{max_show_images_per_step} images per step** "
            f"(across all show() calls combined). Excess images are dropped."
        )
    if not budget_lines:
        budget_lines.append(
            "- Pass only the frames the next reasoning step needs to see; "
            "unrelated frames make the next step harder to read."
        )
    budget_text = "\n".join(budget_lines)

    var = "InputImages_1" if num_videos > 1 else "InputImages"

    return f"""## Visual Inspection — `show()` (PRIMARY)

`show(visual_input)` — display image(s) inline in the next feedback message so **you can see them yourself**.

| Argument | Type | Description |
|----------|------|-------------|
| `visual_input` | image, list of images, or `VisualFeedback` | What to display |

`show()` is your **primary way to see visual content**. Use it liberally:
- **Visual grounding**: `show({var}[idx])` to see annotated frames and identify objects yourself.
- **Intermediate results**: `show(recon.bev_visual())` for the top-down BEV, or `show(tools.Reconstruct.bbox_overlay(recon, frame=idx))` to inspect the grounded object boxes.
- **Matplotlib plots**: `plt.show()` is **auto-captured** — any figure is rendered and shown inline.
{budget_text}
- **NEVER pass large lists** like `show({var}[:30])`. Select only the most informative frames: `show({var}[0], {var}[15], {var}[31])`."""


def robust_computation_section() -> str:
    return """## Robust Computation Principles

- **Use `np.median()` over `np.mean()`** for all aggregations. Prefer higher-`conf` instances and drop low-confidence ones before combining.
- **Never trust a single frame.** Compare across multiple frames — consistent values are reliable, one-off values are noise.
- **Reason in metric units, not pixels.** Always convert to meters/degrees/m·s⁻¹ before judging significance (e.g., a 2px shift is meaningless without the object's distance in meters).
- **Sanity-check magnitudes** (e.g., pedestrian ~1-2 m/s, car ~10-30 m/s, angular noise < 2°, real rotation > 5°). If results violate common sense, suspect bad inputs.
- **Print values before concluding.** When margins are small relative to measurement noise, acknowledge low confidence."""


def code_rules_section(num_videos: int = 1) -> str:
    if num_videos > 1:
        if num_videos <= 4:
            input_names = ", ".join(f"`InputImages_{i}`" for i in range(1, num_videos + 1))
        else:
            input_names = f"`InputImages_1`, `InputImages_2`, ..., `InputImages_{num_videos}`"
        reserved = f"`feedback`, `tools`, {input_names}, `Metadata`, `ReturnAnswer`, `show`, `RefImages`"
    else:
        reserved = "`feedback`, `tools`, `InputImages`, `Metadata`, `ReturnAnswer`, `show`, `RefImages`"
    return f"""## Code Rules

- **Pre-imported** (do NOT re-import): `numpy as np`, `math`, `collections`, `itertools`, `functools`, `matplotlib`, `matplotlib.pyplot as plt`, `scipy` (with `ndimage`, `spatial`, `signal`, `optimize`)
- **FORBIDDEN**: os, subprocess, sys, torch, open(), file I/O, exec(), eval()
- Use `print()` for debug output. Variables with `_` prefix are private.
- One logical step per response. Keep code concise.
- **NEVER** reassign built-in names: {reserved}."""
