# Adjacent-Shot Continuity

Canonical sources are:

- identity from `cast[]`
- wardrobe from `scenes[].wardrobe`
- setting, lighting, and palette from `scenes[]`
- visible per-shot conditions from `reference_image.cast_scene_table`

Every shot records only the continuity state needed to generate and join it:

```json
{
  "link_mode": "start|continuous|cutaway|time_jump|location_change|concept_transform|montage",
  "link_reason": "",
  "time_state": "",
  "screen_direction": "left_to_right|right_to_left|toward_camera|away_camera|static|mixed",
  "wardrobe_state": {},
  "prop_state_in": {},
  "prop_state_out": {},
  "blocking_in": {},
  "blocking_out": {},
  "action_in": "",
  "action_out": "",
  "gaze_in": "",
  "gaze_out": ""
}
```

For `continuous` links, incoming action, props, blocking, wardrobe, time, gaze, and screen direction must agree with the previous shot. For a deliberate jump, state the visible reason and use an appropriate transition. Do not add continuity ledgers that duplicate these fields.
