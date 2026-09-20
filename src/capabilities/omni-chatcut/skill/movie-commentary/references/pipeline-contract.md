# Pipeline Contract

## Ownership

| Concern | Owner |
|---|---|
| source probe, credits/subtitle measurement, chronological Omni evidence | source-analysis workflow |
| narration, locators, audio decisions, plan consistency | commentary-authoring workflow / planner |
| TTS, source re-pinning, subtitles, audio mix, shard MP4/report | rendering workflow / executor |
| state routing, immutable handoffs, final assembly and independent QA | root Skill / orchestrator |

## Durable handoffs

- Analysis → authoring: `project.json`, `plan/execution_facts.json`, and complete `plan/watch_notes/`.
- Authoring → execution: validator-approved `plan/editing_plan.json`, `plan/narration_script.md`, and
  immutable `shards/shard_NN.json`.
- Execution → completion: one successful `exec_report.json` and playable MP4 per shard, final MP4, and
  `full/orchestrator_qa_full.json` with every required check true.

Changing source or execution facts invalidates every downstream artifact. Changing narration/plan
invalidates shards and delivery. Changing only a failed shard leaves successful compatible shards reusable.

## Evidence and degradation

Omni tools consume files, not implicit source ranges. Trim each analysis/search interval to a bounded local
clip and map relative timestamps back to absolute source time. If Omni becomes unavailable after complete
planner evidence was persisted, an executor may locally re-pin only inside an evidenced interval and must
record `degraded_local_repin` plus the external error. Missing evidence blocks the segment.
