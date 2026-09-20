<!-- Internal workflow: loaded by the root Omni ChatCut Movie Commentary Skill. -->
# Commentary Authoring Workflow

Dispatch exactly one movie-commentary planner. Give it the project manifest, execution facts, complete
watch notes, user brief, language, target duration, and audio defaults. The orchestrator must not write or
repair narration. Return plan defects to the same planner.

The planner writes `plan/editing_plan.json` using schema
`omni-chatcut/movie-commentary-plan/v1`, plus `plan/narration_script.md`. Required plan fields are:

```json
{
  "schema": "omni-chatcut/movie-commentary-plan/v1",
  "source_movie": "/absolute/source.mp4",
  "language": "zh",
  "segments": [
    {
      "segment_id": "SEG_0001",
      "narration": {"text": "...", "estimated_duration_sec": 12.0},
      "visual_plan": {
        "target": "searchable action",
        "rough_interval_sec": [100.0, 180.0],
        "movie_locator": {
          "scene_anchor": "...",
          "visible_cues": ["..."],
          "audio_cues": ["..."],
          "evidence_refs": ["plan/watch_notes/chunk_001.md"]
        }
      },
      "audio_plan": {"source_audio_mode": "ducked_bed", "bgm_mode": "none"}
    }
  ]
}
```

Segment IDs are contiguous. Narration is concise causal retelling, not a scene list; names, relationships,
motives, dialogue, and ending require evidence. `rough_interval_sec` is advisory and may be locally
non-monotonic for intercut action; precise cuts belong to the executor. All evidence paths remain below
`plan/watch_notes/`. Every cut stays before `source_cut_max_sec`.

Use `highlight_sync` only for a pivotal original line, normally 3–8 seconds, between complete narration
sentences. With no licensed BGM manifest, every segment uses `bgm_mode: none`.

Call `validate_movie_commentary_plan`. After it passes, call `shard_movie_commentary_plan`; never hand-edit
the frozen shards. Plan-only stops after validation.
