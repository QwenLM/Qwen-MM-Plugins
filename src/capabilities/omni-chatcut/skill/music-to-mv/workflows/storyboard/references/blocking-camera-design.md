# Blocking, Action, and Camera

Use only as much spatial detail as downstream generation needs.

For complex or recurring scenes, define named zones, fixed objects, entrances/exits, default axis, and safe camera side. Shot blocking may reference only those zones.

Every shot's `action_design` states:

- `playable_verb`
- `start_physical_state`
- ordered `action_steps`
- `end_physical_state`
- optional generation risks and a simpler fallback

Describe behavior, not internal emotion. Replace “sad” with a visible action, posture, gaze, breath, or interaction.

`camera_geometry` states start/end position, height, lens/FOV, axis side, frame layers, focus subject, and reveal timing when these affect the result. Camera movement should reveal space, follow action, transfer attention, intensify a music event, or change distance; it cannot replace subject action.

For multi-character or choreography shots, record start/end positions, facing, gaze, travel path, prop ownership, pair distance, and occlusion risk when applicable. Reduce instructions when they do not change the generated image or edit.
