---
name: qwen-mm-plugins-omni-chatcut-movie-commentary
description: "Turn an existing movie or long-form video into a finished narrated commentary cut: build complete chronological Omni evidence, author a grounded narration plan, render resumable shards with subtitles and source audio, assemble them, or resume and diagnose an existing project."
---

# Movie Commentary

Own one durable project and route it by state:

```text
source movie → complete Omni evidence → validated plan → rendered shards → final commentary + QA
```

Inspect first:

```bash
python3 <skill-root>/scripts/inspect_movie_commentary_state.py <project-dir>
```

Use the first applicable route: validate an existing final delivery; resume incomplete shard execution;
render a validated plan; author from complete source evidence; or analyze a new source. Never infer
completion from filenames alone. Read [pipeline-contract.md](references/pipeline-contract.md) before
accepting or invalidating artifacts.

## Internal workflows

- Complete-film evidence: [source-analysis/WORKFLOW.md](workflows/source-analysis/WORKFLOW.md)
- Narration and edit plan: [commentary-authoring/WORKFLOW.md](workflows/commentary-authoring/WORKFLOW.md)
- TTS, shard rendering, assembly, and QA: [rendering/WORKFLOW.md](workflows/rendering/WORKFLOW.md)

Load only the workflow needed by current state. Use the capability's atomic MCP tools to initialize and
probe a project, validate plans, freeze shards, and validate delivery. Use the named movie-commentary
planner/executor agents when the harness exposes them; otherwise dispatch genuinely separate generic
subagents with the corresponding workflow contract. Do not collapse both roles into one agent.

## Project and authorization

Use [manifest-template.json](assets/manifest-template.json). A new project root must be empty. A non-empty
root requires explicit resume intent. Full production authorizes the required Omni, TTS, and local render
calls; analysis-only and plan-only targets stop before TTS/render. Credentials never enter artifacts.

The default Chinese voice is `zh-CN-YunxiNeural` at `+20%` with no additional atempo. Without a licensed
BGM manifest, BGM is `none`. Preserve source aspect ratio. Commentary subtitles must be measured against
the actual source subtitle band.

## Completion

A final result requires every planned segment resolved, reports matching the frozen shard contract, full
decode, audio/video duration agreement, expected frame count, measured loudness/true peak, black/freeze/
silence checks, seam inspection, subtitle placement, and original-dialogue highlight checks. Fake green is
worse than a blocked project.
