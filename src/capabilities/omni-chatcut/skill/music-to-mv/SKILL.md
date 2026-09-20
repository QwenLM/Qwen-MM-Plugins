---
name: qwen-mm-plugins-omni-chatcut-music-to-mv
description: "Turn local music, an existing music caption, or a validated storyboard into an MV: analyze music for visual planning, author or validate a timed storyboard, generate or resume image and video assets, assemble the final MV, or diagnose an existing Music2MV project."
---

# Music-to-MV

Own the project state and route work through three internal workflows. The workflow order is flexible,
but their artifact dependencies are not:

```text
music/audio → completed caption or music map → validated storyboard → generated assets/final MV
```

## Python runtime

Before running local Python scripts, call `get_music2mv_runtime` with `{}`. Replace `python3`
in this skill and its references with the returned `python_executable` unchanged (do not resolve
its symlink). For `run_mv_pipeline.py`, also pass `--expected-python-prefix` with the returned
`python_prefix`; a mismatch stops execution before dependency imports. Query again after an MCP
restart/reinstall; if the tool is unavailable, restore the MCP connection before running scripts.

## Route by state

Determine the requested target: `analysis_only`, `storyboard_only`, `final_mv`, or `resume`. Inventory
the project before choosing a workflow:

```bash
python3 <skill-root>/scripts/inspect_music2mv_state.py <project-dir>
```

Use the first applicable route:

1. A final MV exists: validate/report it; do not regenerate it merely to normalize paths.
2. Resumable execution state exists: continue the video-generation workflow.
3. A validated storyboard exists: deliver it or execute it according to the target.
4. A completed caption/music map exists: author or revise the storyboard.
5. An incomplete project-backed caption run exists: resume the music-caption workflow from its last
   durable phase when its source audio is still readable.
6. Readable source audio exists: run music captioning, then continue only as far as requested.
7. Otherwise request the missing audio, caption, storyboard, or project directory.

Read [pipeline-contract.md](references/pipeline-contract.md) when deciding artifact validity or
invalidation. Read [gates.md](references/gates.md) before changing stages or using a remote provider.
Before any remote call, read [model-configuration.md](references/model-configuration.md) and use the
same unified model-connection file for Omni, image, and video calls.

## Internal workflows

Load only the workflow needed for the current state:

- Music evidence and caption: [music-caption/WORKFLOW.md](workflows/music-caption/WORKFLOW.md)
- Creative direction and validated storyboard: [storyboard/WORKFLOW.md](workflows/storyboard/WORKFLOW.md)
- Identity assets and selectable video-provider execution, optional Omni shot QC, resume, assembly, and technical QC:
  [video-generation/WORKFLOW.md](workflows/video-generation/WORKFLOW.md)

These are internal workflow documents, not separately discoverable Skills. They may be skipped,
repeated, or revisited when their preconditions and invalidation rules permit it.
Treat remote models as execution details. Reuse generated artifacts only when their provider, model,
parameters, inputs, and execution signature remain compatible.
Every editorial shot maps to exactly one video-provider request and one independently reviewable file;
the local assembler, never the provider, owns boundaries between shots.

## Provider defaults and Seedance characters

Default to Qwen Image 3.0 (`qwen_image_3`) and Wan 3.0 (`wan3`) unless the user or existing
project configuration selects another provider. The default DashScope connections require a
Beijing-region `DASHSCOPE_API_KEY`, with no workspace ID setup. Qwen Image and Wan use the shared
`https://dashscope.aliyuncs.com` domain with native API paths; Omni uses the OpenAI-compatible path.
Seedream and Seedance connect directly to Volcengine Ark.
When using Seedance, use existing Ark image asset IDs for fixed characters and skip identity-image
generation. Prefer user-specified assets; otherwise select from the local character pool. Read
[character selection](references/seedance-character-search.md) before casting and
[Seedance execution](workflows/video-generation/references/seedance.md) before generation.
Website browsing and uploads are manual user actions; the skill only suggests these options.

## Profiles

Default to `standard`. When the user requests a known variant, read [profiles.md](references/profiles.md)
and record the selected policy from [profiles.json](assets/profiles.json) in the project manifest.
Profiles change optional evidence and presentation policy; they never waive structural validation,
technical safety checks, or required upstream inputs.

Omni semantic shot QC is not a profile default. It is off unless the user explicitly requests it or
supplies a run config with `quality_control.enabled=true`. Do not enable it merely because the target
is `final_mv`, the selected profile is `standard`, or additional review might improve quality.

## Project state

Maintain `music2mv-manifest.json` using [manifest-template.json](assets/manifest-template.json). Prefer:

```text
project/
  music2mv-manifest.json
  inputs/
  analysis/music-caption/
  authoring/
  execution/
  work/music-caption/
```

Preserve successful remote task identifiers and immutable outputs. If an upstream artifact changes,
mark its downstream artifacts stale rather than silently reusing them.
Keep resumable caption evidence and canonical caption artifacts under `analysis/music-caption/`.
Use `work/music-caption/<run-id>/` only for disposable audio clips. A one-off caption request without
a project may use a unique system temporary directory, but that run is not durable project state.

## Authorization boundary

Music analysis, local validation, dry runs, and planning do not authorize paid generation. An explicit
request to create the final MV or resume an already requested generation authorizes the corresponding
provider calls. It does not by itself opt into paid Omni shot review or review-triggered video
regeneration. If that intent is ambiguous, stop before the first paid call and ask once. Never expose
credentials in commands, manifests, logs, previews, or generated reports.

## Completion

Stop at the requested target. A final-MV result requires validated authoring, execution compatibility,
the configured semantic-QC policy, successful assembly, and technical QC. Semantic QC is skipped by
default; report it as skipped without implying that the user declined a required stage. Technical
success alone is not proof that generated material matches
the storyboard. When the selected profile enables subtitles, burn the accepted sentence-level lyrics
after picture and soundtrack assembly; only a validated `no_lyrics` result may complete without them.
