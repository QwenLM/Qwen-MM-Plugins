---
name: qwen-mm-plugins-video-spatio
description: "Reason about distance, size, orientation, relative position, camera motion, and object counts in images and video. Ground objects and estimate their depths, then use stateless geometry tools to compute and inspect a coarse scene."
---

# Video-Spatio — spatial reasoning

Use this Skill when a question needs spatial reasoning: distance, size, facing, left/right/front/behind,
camera versus object motion, cross-frame counting, or viewpoint-dependent visibility. Answer appearance
questions directly from the images. Read the `qwen-mm-plugins-video-spatio` tool schemas for arguments.

The host model supplies object boxes, distance estimates, and camera-motion estimates. Geometry tools
calculate from those inputs; they do not independently measure the scene. No GPU perception server is
required. VLM-backed tools use the configured OpenAI-compatible endpoint and may make several calls.

## Build a scene

1. Inspect the image or sample video frames. If installed, `core` provides `read_video` and `save_view`.
   Otherwise use available image/video tools and provide local frame paths.
2. Ground the relevant objects in each frame, with one list per frame:
   `{"label":"chair","bbox":[400,300,600,800],"depth_m":2.3}`.
3. Call `build_scene(frames=..., objects_by_frame=...)`. All frames must have the same dimensions.
   Use unique `frame_indices` when retaining original video indices.
4. For a moving camera, supply one `camera_motions` entry per adjacent pair. Translations are in the
   preceding camera's frame: `forward_m` is positive forward, `right_m` positive right, and `yaw_deg`
   positive for a right turn. Omitting motions assumes a static camera; it does not estimate motion.
5. Pass the returned `scene` JSON to subsequent tools, or save it and pass `scene_file`.

Coordinates:

- Boxes are `[x1,y1,x2,y2]`, top-left to bottom-right, with x horizontal and y vertical.
  The default is **0–1000 normalized** at every image resolution. Explicitly set `bbox_format="pixels"`
  or `bbox_format="normalized"` for pixels or 0–1 coordinates. Do not mix units in one scene.
- `depth_m` is a positive camera-to-object distance estimate in meters. Preserve uncertainty in the
  answer; visual depth estimates are not calibrated measurements.
- World coordinates use +Y up, +X right, and **-Z forward** from the first camera. BEV uses `(x,z)`.
  `pos_bev=[x,z]` and `yaw_deg` describe each camera in that common world frame.
- The scene assumes a 60° horizontal field of view and planar camera motion. Pitch is recorded but
  does not change the planar geometry. Large pitch or uncertain motion makes the BEV less reliable.

## Choose the analysis

| Question | Tools and checks |
|---|---|
| Relative layout | `visualize_bev(scene, viewpoint={"frame":N})` shows the selected camera's forward/right axes. |
| Virtual viewpoint | Use `viewpoint={"at":"door","facing":"table"}` or `facing_away` for the opposite direction. Resolve an object's actual facing before using its perspective. |
| Distance | `triangulate(scene,target,frame_a,frame_b)` needs the same stationary object and a real camera baseline. Check `reliable`; parallel, opposed, or backward rays cannot establish a reliable position. |
| Known-size calibration | `calibrate_scale(scene,target,known_size_m,dim)` returns a scale factor to apply to distance estimates. |
| Object motion | `object_world_motion` compares estimated world positions after accounting for supplied camera poses. Recheck correspondence and camera estimates before declaring movement. |
| Camera motion | `camera_motion` summarizes the poses already in the scene. It is not independent evidence validating the original motion estimates. |
| Facing | `orient_facing(image,target)` uses VLM calls. Inspect the full image for body/object orientation. |
| Count and identity | `count_objects` counts grounded instances; `match_entities` groups sightings across views. Inspect repeated nearby objects before accepting deduplication. |
| Choose frames | `select_keyframes` supports uniform, motion, coverage, and covisibility strategies. |
| Temporal changes | `scene_map` provides cognitive maps, appearance order, frame differences, and VLM event localization. |
| Visibility | `view_reason` reasons from an object's viewpoint. Sparse boxes cannot establish a complete occlusion model. |
| Motion tracks | `motion` segments frame differences or extrapolates supplied points. Extrapolation assumes the fitted motion continues. |
| Additional views | `render_scene_views` renders a coarse layout from the scene. |

For `visualize_bev`, use labels that identify the intended instance unambiguously. Prefer one frame
when it contains all relevant objects. A multi-frame layout additionally depends on camera estimates
and correct cross-frame correspondence.

## Exploration and approach suggestions

Use `assess_coverage` → `plan_exploration` when the target has not been observed, and
`assess_reachable` when it is grounded. `mobile_manip` can suggest navigation, movement, staging,
tracking, or search steps. These tools return suggestions; they do not move hardware.

A distance-based reachability result does not establish an obstacle-free route or a feasible grasp.
For an interactive task, inspect the next observation and rebuild the scene after each movement.
For an offline video, report what additional frames would resolve the question. Room priors and
unobserved-space suggestions are hypotheses, not evidence that an object exists there.

## Cross-check and answer

Check boxes visually before relying on geometry. `verify_grounding(image,bbox,label)` offers a VLM
cross-check using 0–1000 normalized coordinates. Use neutral questions and allow "not visible".

Distinguish image coordinates, a particular camera's perspective, world coordinates, and an object's
perspective. For a virtual viewpoint at A facing B in world `(x,z)`:

```text
forward = normalize(B - A)
right = [-forward_z, forward_x]
dot(C - A, forward): positive = front, negative = behind
dot(C - A, right): positive = right, negative = left
```

Geometry and a visualization generated from the same estimates are not independent observations.
If evidence disagrees, recheck grounding, units, object identity, and camera motion. State assumptions,
units, uncertainty, and limits in the answer; use the response format the user requested. Do not force
a precise number or a choice when the available observations cannot support it.
