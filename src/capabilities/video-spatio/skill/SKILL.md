---
name: qwen-mm-plugins-video-spatio
description: "3D spatial reasoning over images/video — distance, size, orientation, relative position (left/right/front/behind), camera motion, and 3D counting. Model-free: YOU do the perception (ground objects + estimate depth), stateless geometry tools do the math, you reason over the results."
---

# Video-Spatio — 3D Spatial Reasoning

Check the `qwen-mm-plugins-video-spatio` tools in your tool list for full schemas. YOU are the reasoning
loop: get frames, ground the objects yourself, feed them to the geometry tools, cross-check, answer.

**Model-free / prompt-only**: there is NO perception model (no SAM/depth/dense-reconstruction server). YOU
provide perception — you are a strong VLM: ground the question's objects and estimate each one's metric
depth, then `build_scene` turns your estimates into an instance-level 3D scene the geometry tools operate on.

## ROUTING
Use this skill when the answer needs **3D geometry**: distance, size, orientation/facing, left/right/front/behind,
"did the object or the camera move", 3D counting, occlusion/line-of-sight. For pure semantics (what color, what
is this) just look — you don't need these tools. For frames use `read_video` / `save_view` (core skill).

## COORDINATE SYSTEM (never guess — always ground)
- **bbox / point**: `[x1,y1,x2,y2]`, **0-1000 normalized, x FIRST then y, origin TOP-LEFT** (x: 0=left…1000=right,
  y: 0=top…1000=bottom). A point is the bbox centre. Keep `x=`/`y=` labels when you write coordinates.
- **World frame**: **+Y up**, **-Z = first camera's forward**, **+X = right**. The BEV is the world `(x, z)` plane.
- **Camera pose** (per frame): `pos_bev=[x,z]` meters; `yaw_deg` (+ = turned right, forward = world -Z at yaw 0); FOV≈60°.
- **Four spaces that disagree — decide which the question means BEFORE computing**:
  - *Pixel* — where things appear; conflates camera vs object motion → never use as evidence for real-world claims.
  - *Camera* — left/right/front/behind from ONE viewpoint; changes every frame → always name which frame's camera.
  - *World* — fixed global coords; where real distances/speeds/trajectories live; consistent across time.
  - *Object perspective* — relative to an object's facing; an object facing the camera has left/right **mirrored** →
    determine facing first, then define left/right/front/behind from that heading.

## PERCEPTION — how to describe objects for `build_scene`
For each frame, produce a list of the salient / question-relevant objects:
```
{"label": "chair", "bbox": [x1,y1,x2,y2], "depth_m": 2.3}
```
bbox in the convention above (0-1000, x-first, TL origin); `depth_m` = your best distance estimate from the camera,
in meters (positive float). Ground the question's objects explicitly; a wrong bbox/depth is the #1 cause of a
flipped answer, so verify (see verify_grounding) before trusting geometry.

## WORKFLOW — Decision tree (CLASSIFY the question first, then act)
Always `read_video` / `save_view` (core) to get frames first. Then:

### Decision 1 — what does the question ask?
- **Understand / Judge** (pure appearance / identity / object state open-closed / contact / action recognition /
  scene description / non-spatial MCQ "what is / which is") → **answer from direct visual perception; do NOT run
  build_scene/geometry.** Still LOOK properly: `save_view`/`crop` to the relevant region at high res, take 2-3
  independent reads (across frames/phrasings) and go with the majority — never one-shot a bare guess.
- **Quantify / Locate** (distance, size, count, angle, trajectory, **left/right/front/behind, facing/orientation**,
  "which is closer/farther", camera motion) → **use tools** → Decision 2.
- **Plan / Execute** (navigate / manipulate action sequence) → Decision 2, then `mobile_manip`.
- **Routing rule (bias to tools):** if the question mentions distance/size/count/direction/left-right/facing/
  "between"/"closer/farther"/camera-movement → it is **Quantify (tools), NOT direct**. When unsure → treat as Quantify.

### Decision 2 — ground the objects (for Quantify/Plan)
Ground the question's objects yourself (`bbox_1000` + `depth_m` per frame, format above) →
`build_scene(objects_by_frame, frames, camera_motions=...)` → `scene`. **VERIFY before trusting**:
`verify_grounding(image,bbox,label)` (and/or look) — a wrong bbox/depth is the #1 cause of a flipped
answer; re-ground if off. Counting → `count_objects`; cross-frame identity → `match_entities`; pick
informative frames → `select_keyframes`.

**Multi-frame? Estimate camera motion.** For every adjacent frame pair i→i+1, quickly judge how the
camera moved and pass `camera_motions=[{yaw_deg, forward_m, right_m, pitch_deg?}, ...]` (len = N-1).
`yaw_deg`=+right/−left horizontal turn; `forward_m`/`right_m`=planar meters; `pitch_deg` is recorded
but not used by BEV (top-down is ground-plane 3-DoF). If you skip this, `build_scene` returns identity
cameras (only OK for single-view / static), and triangulate/camera_motion won't be usable — the summary
line will say `cameras=identity`. If `|pitch| ≥ 20°` on any frame, the summary will flag BEV as
unreliable — prefer monocular Pattern A + visual reasoning.

**YAW is THE critical input for every left/right/front/behind & facing question.** Each frame's
`yaw_deg` must be established — via EITHER (a) geometry: `camera_motion(scene,i,j)` between frames, OR
(b) YOUR direct visual estimate of the relative turn — and passed in `camera_motions`. **NEVER leave
identity cameras (yaw=0) for a direction question**: with identity cameras the world placement and the
BEV quadrant are meaningless. When you have both, cross-check the geometric yaw vs your predicted yaw;
if they disagree by more than ~20°, re-examine the grounding/turn estimate before trusting the quadrant.

### Decision 3 — analyse by question shape (table below), then compute
All spatial analysis = entities on the world BEV → choose reference frame → compute.
- **Establish yaw FIRST, and prefer a single frame when possible.** The BEV quadrant read is only as
  reliable as each frame's `yaw_deg` (see Decision 2). If the standpoint object, the facing object, and
  the target are ALL visible in ONE frame, read their in-frame bearings directly (Pattern A, camera-
  relative) — this needs NO cross-frame yaw and is the most robust for left/right/front/behind. Use the
  multi-frame BEV quadrant when the objects span different frames (then yaw MUST be established, not identity).
- **Left/right/front/behind (FIRST-CHOICE recipe — read a quadrant, don't hand-compute):**
  `visualize_bev(scene, viewpoint=...)` draws a **dashed forward/right cross-hair** at the viewpoint
  with **Front/Back/Left/Right** quadrant labels; each object's quadrant IS the answer. Viewpoint forms:
  - Camera frame: `viewpoint={"frame": N}` — "as seen from camera N".
  - Virtual, facing an object/point/angle: `viewpoint={"at": "<label>"|[x,z], "facing": "<label>"|[x,z]|<heading_deg>}`
    e.g. "stand at the door facing the tub" → `{"at":"door","facing":"tub"}`.
  - **Virtual, facing AWAY from an object** ("standing at X, facing the opposite direction of Y" — a
    common revsi/site pattern): use `facing_away` — the tool auto-adds 180°. Do NOT try to hand-flip
    the heading (that's the #1 source of 180°-inverted answers).
    e.g. "at floor lamp, facing opposite direction of desk" → `{"at":"floor lamp","facing_away":"desk"}`.
  Only fall back to hand-computed Pattern A/B when the BEV output is flagged unreliable (identity cameras
  or `|pitch|≥20°`), or the scene has no camera_motions. **Never decide left/right from a bare VLM look.**
- **Facing / orientation** → `orient_facing(image,target)` (holistic, majority-voted).
- **Viewpoint / visibility / occlusion** → `view_reason(scene, op=..., viewpoint=...)`; render layout too.

### Decision 4 — embodied action planning (only if the task needs robot actions)
All action planning goes through `mobile_manip(scene, op=..., target="<label>")`. Pick by target visibility
and task shape:

1. **Target NOT in the scene / not yet observed** (look-for / find X):
   `mobile_manip(scene, op="plan_active_search", target="<label>", max_moves=3)`
   Reads the camera trajectory from `scene.cameras` (which sides you have vs haven't turned toward), and
   returns `{search_plan:[{action, reason}], skill_sequence:["observation(X)","turn_*()","look_for(X)"]}`.
   Execute → re-observe → re-build scene → repeat until found.

2. **Target visible, need a single-leg locomotion** (walk to X):
   `mobile_manip(scene, op="plan_navigation", target="<label>", frame=<int>?)`
   Pure geometry — from the target's bearing + `depth_m` it emits `["turn_left()"|"turn_right()"|
   "turn_back()"|nothing, "go_forward(<label>)"]`. Do NOT hand-derive turns from angles.

3. **Multi-leg / route-planning question** (given start + goal + waypoints, pick the action string):
   Call `plan_navigation` **once per leg**, using each successive waypoint as `target`. Concatenate the
   `skill_sequence`s → this is your route. Match against the MCQ options.

4. **Manipulation prep — is the target already reachable?**:
   `mobile_manip(scene, op="plan_movement", target="<label>", max_reach_m=1.0)`
   Returns `reachable:true, steps=[]` when `depth_m ≤ max_reach_m`; otherwise the full turn+forward plan.
   Use for "can the robot pick X now" or "does the robot need to move first" questions.

5. **Pre-contact staging around an object** (from which side to approach):
   `mobile_manip(scene, op="suggest_approach", target="<label>")` — scores 4 approach directions.

Related observation ops (for grounding before planning):
`mobile_manip(op="track_object_trajectory"|"check_object_in_view"|"search_object_across_frames"|"reachability", target=...)`.

#### Offline / open-loop variant (no env step — only JUDGE + SUGGEST)
When there is NO environment to step (offline benchmark: you cannot actually turn/walk, you only
have the frames and the accumulated `scene`), and the question is "where would I go / can I reach it",
prefer these three **pure-geometry, model-free** tools over the recon+VLM `mobile_manip` ops. They read
`scene` directly and return a judgment plus a concrete suggestion (including `offline_assessment` with
`specific_data_calls` = which `read_video`/`save_view`/`select_keyframes`/`build_scene` to run next, and a
`fallback_answer_hint` to commit to if forced). Chain them:

1. **Is what I've seen enough to answer?** — `assess_coverage(scene, target="<label>")` → `coverage_deg`,
   `unobserved_sectors`, `sufficient_for_target`, `target_hint` (if the target's likely room is un-observed).
2. **Not enough — where to explore?** (haven't seen the room / the object) — `plan_exploration(scene,
   target="<label>")` → `strategy ∈ {found, spin_scan (turn ~360° in place), traverse_gateway (head to a
   door into an unobserved sector), go_forward}` + `skill_sequence`. This is the structural upgrade to
   `plan_active_search` (which only does small local left/right turns).
3. **Manipulation prep — can I move to a position to OPERATE the target?** — `assess_reachable(scene,
   target="<label>", max_reach_m=1.0)` → `reachable_now` (an observed viewpoint is already within reach) /
   `reachable_after_move` (grounded but far → one turn+go_forward leg + `distance_to_close_m`) /
   `target_not_grounded` (never seen → go back to step 1/2 first). Scope: judges only whether an operable
   vantage is reachable, not whether the manipulation itself succeeds.

These are additive: online/embodied tasks still use the `mobile_manip` ops above; non-embodied Quantify
questions (distance/count/left-right/facing) are unaffected — they never enter Decision 4.

### Finish — cross-check + COMMIT (STRICT format — mandatory)
Require **≥2 independent lines of evidence to agree**. If geometry is degraded (build_scene returned identity cameras /
`triangulation_angle_deg` < 8° / very noisy depth), SAY so and lean on monocular Pattern A + careful visual reasoning
rather than the unreliable multi-view number.

Then end your response with a machine-parseable line. Nothing else on that line, no markdown fences, no bold.
Use exactly ONE of these formats (matching the question type):

- Multiple-choice (has A/B/C/D options):
  `FINAL ANSWER: <LETTER>`   (a single uppercase letter A/B/C/D — nothing more)
  e.g. `FINAL ANSWER: B`

- Numeric (distance / size / area / count / angle):
  `FINAL ANSWER: <NUMBER>`   (a single number, no units, no range, no "≈", no words)
  e.g. `FINAL ANSWER: 1.3`     (a meter distance)
       `FINAL ANSWER: 42`      (a count)
       `FINAL ANSWER: 10.2`    (an area in m² — omit the unit)

Rules:
- One number OR one letter — never both, never a range like `10-11`, never "options not given".
- If the question asks for meters/cm/m²/degrees, still write ONLY the number in the requested unit (see the question).
- Put brief reasoning + tools used on the lines BEFORE this final line, not after. The grader looks only at this line.

## DECISION — question shape → tool (after grounding + build_scene)
| Question | Tool |
|---|---|
| Distance object↔object / object↔camera, absolute meters | `triangulate(scene,target,frame_a,frame_b)` (2-view; if `triangulation_angle_deg` < 8° it's unreliable → fall back to monocular depth_m + bearing, Pattern A). Known real size? `calibrate_scale(scene,target,known_size_m)` → multiply by `scale_factor`. |
| Left/right/front/behind (camera or virtual viewpoint) | `visualize_bev(scene)` to see layout, then Pattern A (camera) or Pattern B (virtual). Geometry decides; a VLM read only cross-checks. |
| "Did the OBJECT move or the CAMERA?" / where did it move | `object_world_motion(scene,target,frame_a,frame_b)` (removes ego-motion); `visualize_bev(scene, ids=[obj])` to see both cameras + object. |
| How did the CAMERA move/turn between frames | `camera_motion(scene,frame_i,frame_j)` → translation + rotation + dominant direction. |
| Object facing / orientation | `orient_facing(image,target)` (holistic, majority-voted). "From X's point of view" → read facing, then Pattern B with A's facing = X's heading. |
| Count objects (3D, dedup across frames) | `count_objects(scene,label?)`; `match_entities` to dedupe the same object across frames. |
| Motion segmentation / trajectory | `motion(frames=...)` (segment) or `motion(past_points=...)` (extrapolate). |
| Scene layout / cognitive map / appearance order / frame diff / temporal localization | `scene_map(scene, op=cognitive_map|appearance_order|diff_frames|locate_event, ...)`. |
| Visible-from / occlusion / from-a-viewpoint | `view_reason(scene, op=scene_layout|visible_from|line_of_sight|from_viewpoint, viewpoint=..., ...)`. |
| Embodied: approach/track/reachability | `mobile_manip(scene, op=..., target=...)`. |
| Embodied OFFLINE (no env step): enough-to-answer? / where to explore / can I reach to operate | `assess_coverage` → `plan_exploration` → `assess_reachable` (pure geometry, model-free; see Decision 4 offline variant). |
| See the scene from another angle | `render_scene_views(scene, faces=["front","top"])`. |

## GEOMETRY CORES (compute yourself when a tool doesn't cover it)
**Pattern A — camera-relative bearing/offset from an instance** (fov_deg≈60):
```
bearing_deg = (point_1000[0]/1000 - 0.5) * fov_deg   # + = right of image centre
fwd_m   = depth_m * cos(bearing_deg)                 # + = FRONT
right_m = depth_m * sin(bearing_deg)                 # + = RIGHT, - = LEFT
```
**Pattern B — virtual viewpoint ("stand at A facing B, where is C?")**: convert A,B,C to world BEV (x,z), then
```
fwd = normalize(B - A); right = [fwd_z, -fwd_x]
dot(C-A, fwd)  > 0 FRONT / < 0 BEHIND ;  dot(C-A, right) > 0 RIGHT / < 0 LEFT
```
**Pattern C — camera motion f1→f2**:
```
moved_right   = dx*cos(yaw1) + dz*sin(yaw1)          # + = right
moved_forward = dx*sin(yaw1) - dz*cos(yaw1)          # + = forward
turn_deg = ((yaw2 - yaw1 + 180) % 360) - 180         # + = turned right
```

## RECIPES (ordered tool chains; each = ground → verify → compute → cross-check)
- **Distance A↔B (meters)**: build_scene(both) → verify boxes+depths → `triangulate`; if angle<8° use depth_m+Pattern A;
  known size → `calibrate_scale`; sanity-check magnitude. *Pick two frames with real baseline separation.*
- **Left/right/front/behind**: build_scene → **verify boxes AND depths first** → `visualize_bev` → Pattern A (camera) or
  B (virtual). *Object-perspective mirrors left/right — resolve facing first.*
- **Object vs camera motion**: build_scene(both frames) → `object_world_motion` → `visualize_bev(ids=[obj])`; reason in
  world meters. *Never infer motion from pixel shift.*
- **Orientation**: `orient_facing` (full image, majority vote) → return directly; egocentric → Pattern B.
- **Counting**: `count_objects` (+ `match_entities` to dedupe across frames) → verify on representative frames.
- **Temporal / change / visibility**: `scene_map(op=locate_event|diff_frames)` / `view_reason(op=line_of_sight)` → verify visually.

## EVIDENCE HIERARCHY (cross-validation)
No single source is reliable alone — get **≥2 independent lines to agree** before answering.
- Visual perception: good for identity/appearance/qualitative layout; **unreliable for metric quantities & real-vs-apparent motion**.
- Geometry: good for relative distances/angles; **only as reliable as its inputs** (a mis-grounded object → wrong answer).
- Visualizations (BEV): good for sanity-checking; lossy — "looks wrong" is not proof the data is wrong.
- Metric-distance reliability: `triangulate` (angle ≥ 8°) > `calibrate_scale` (known size) > monocular `depth_m` (coarse fallback).
- When sources disagree: audit the computation chain (right instance? plausible coords? right frame? right coordinate space?),
  then the visual evidence; find the concrete error. **Never override evidence with intuition.**

## ROBUST COMPUTATION
- Aggregate with **median, not mean**; prefer higher-conf instances; drop low-conf before combining.
- Never trust a single frame — consistent-across-frames = reliable, one-offs = noise.
- Reason in **metric units** (m / deg / m·s⁻¹), not pixels. Sanity-check magnitudes (pedestrian ~1-2 m/s, car ~10-30 m/s;
  angular noise < 2°, real rotation > 5°). Print values before concluding; small margins vs noise → say low confidence.

## VLM-read guidance (for perception you do yourself)
Verify presence first (PRESENT/ABSENT/AMBIGUOUS); output labeled `(x=..,y=..)` 0-1000; **"Not visible" is a valid answer** —
a guessed coordinate near a wrong object is a hallucination. Use the FULL image for facing (cropping loses body-pose
context). Neutral phrasing — never embed your expected answer. Multiple consistent reads > one read.
