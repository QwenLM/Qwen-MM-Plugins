<!-- Internal workflow: loaded by the root qwen-mm-plugins-omni-chatcut-music-to-mv Skill; not independently discoverable. -->

# Video Generation Workflow

Execute the validated frozen storyboard without rewriting its story, cast, scenes, timing, lyrics, actions, or camera plan.

## Inputs

- validated storyboard JSON
- original source audio
- output directory
- unified model-connection config and its named credential environment variables
- per-run generation config

If creative work remains, stop and return to the storyboard workflow.

## Execution Model

For Wan, use the configured identity-image provider to create one identity portrait per cast member, and download every temporary result immediately. Scene definitions remain text: each video request receives the complete environment, layout, lighting, palette, material, and shot-specific composition needed for that shot. This lets related shots retain intentional textual continuity without forcing them to reproduce one fixed scene image. Do not generate per-shot images containing principal cast.

When Seedance is selected, follow [seedance.md](references/seedance.md): bind every fixed cast member
in `providers.seedance.official_identity_assets` to an Ark image asset ID before paid work.
Skip portrait generation and private asset registration. Do not use ordinary image URLs, generated
portraits, or silently recovered legacy assets as character references. Explicit user-provided
assets from completed authorized uploads are supported. Describe the chosen assets accurately in
the storyboard. Only an Ark API key is needed; never request AK/SK for this route.

Wan consumes generated identity images; Seedance consumes Ark image asset IDs. Every storyboard segment contains
exactly one editorial shot and becomes exactly one provider task containing:

1. generated identity portraits for Wan, or configured Ark image asset IDs for Seedance, in segment cast order
2. the complete audio interval for that shot
3. one continuous-shot prompt containing the complete scene environment plus exact cast, wardrobe, blocking, action, camera, and mouth state

Preserve the shot's direct `visual_design`, `action_design`, camera relationship, and light/color/material state. For dance shots, also preserve `movement_design`, including dance style, movement quality, connected stages, mapped accents, body channels, and camera protection. For performance shots, preserve `performance_design`, including delivery mode, visible gaze/breath/body/mouth/instrument behavior, and intensity change. Do not collapse rich choreography or performance into generic walking, posing, arm waving, or long holds.

Also carry the concise whole-film premise, driving thread, visual-world logic, assigned development stage, and current visual-unit intent into each request. These provide cross-shot coherence; the shot's concrete visible design remains authoritative for what the provider must generate.

Reject any segment that contains more than one `sub_shot` or `shot_window`. The provider must not create hard
cuts, dissolves, match cuts, another camera setup, or another scene inside a request. The request
duration and reference audio both belong to that one shot. Downloaded results are normalized to the
shot's exact frame allocation, and the local assembler creates every edit boundary. A long shot may
develop continuously through multiple action or visual stages, but it remains one camera take.

Reference responsibilities are strict:

- person references control identity only
- prompt controls scene, wardrobe, action, blocking, camera, and timing

Read [execution-contract.md](references/execution-contract.md),
[model-configuration.md](../../references/model-configuration.md), [api-and-rate-limits.md](references/api-and-rate-limits.md), the selected image/video provider reference, and [subtitles.md](references/subtitles.md) before a paid run. Use [qwen-image30.md](references/qwen-image30.md) for Qwen Image, [seedream.md](references/seedream.md) for Seedream, [wan30.md](references/wan30.md) for Wan, and [seedance.md](references/seedance.md) for Seedance. When `quality_control.enabled=true`, also read and use [shot-quality-control.md](references/shot-quality-control.md).

Treat generation and download as batch queues rather than completing one segment end to end before
starting the next. Submit every authorized segment at the selected provider's configured rate, poll
all in-flight tasks, and download successful results concurrently. Only when
`quality_control.enabled=true`, treat semantic review as another batch queue: send all review-ready
candidates to Omni concurrently up to `quality_control.workers`, apply policy after each result, and
collect rejected segments into one rate-limited parallel regeneration batch. A slow segment must not
prevent independent segments in the same round from being generated or downloaded.

## Supported Scope

- `live_action`
- fictional identities represented by generated portraits for Wan; public virtual portraits or explicitly supplied, authorized adult portrait image assets for Seedance
- one continuous editorial shot per video-provider request
- complete per-shot audio conditioning
- frame-deterministic local shot assembly

Wan identity generation remains limited to fictional adults. Seedance may use authorized adult
real-person assets through the documented Ark enrollment route; do not substitute generated lookalikes
or assume enrollment from an ID alone. This workflow does not cover minors, 2D animation, 3D animation,
or mixed-media identity switching.

## Timing Contract

- Every segment has one shot and one local window `[0, duration]`.
- Send that shot's exact source-audio interval; calculate integer request duration by provider.
- Wan: `max(2, ceil(duration))`, with each source-audio interval between 1 and 15 seconds.
- Seedance: `min(12, max(4, ceil(duration)))`, giving requests of 4–12 seconds. Shots shorter than
  4 seconds are trimmed locally; shots longer than 12 and up to 13 seconds request 12 seconds and
  are slowed across the whole video during frame normalization to fill the shot.
  Shots longer than 13 seconds fail validation.
- Keep each shot within the selected provider's validated duration and reference-audio limits.
- Normalize the result to the shot's exact storyboard frame count.
- Measure the downloaded video stream: slow it uniformly when it is shorter than the target,
  or trim it at its original speed when it is longer. Keep the original soundtrack at its original speed.
- Perform all inter-shot cuts and transitions locally; never delegate them to the provider.
- Lip sync and choreography remain model-generated and require semantic-QC evidence when claimed.

## Workflow

Use the thin launcher at `<workflow-root>/scripts/run_mv_pipeline.py`; it delegates to the packaged
`qwen_mm_plugins_omni_chatcut.music_to_mv.pipeline` implementation while preserving standalone plugin portability.
Apply the root Skill's Python runtime rule to all examples below: replace `python3` with the
`python_executable` from `get_music2mv_runtime`, and add `--expected-python-prefix "<python_prefix>"`
to each runner command. Direct development invocations may omit the check when using a prepared environment.

### 1. Validate and Plan

```bash
python3 <workflow-root>/scripts/validate_execution_storyboard.py <storyboard.json>
python3 <workflow-root>/scripts/run_mv_pipeline.py plan \
  --storyboard <storyboard.json> --audio <audio> --output <output> \
  --model-config <model-config.json> --config <run-config.json>
```

Inspect `semantic_quality_control` in the plan output before any paid call. The default must report
`enabled=false`, `additional_omni_calls=false`, and `video_regeneration_possible=false`. An enabled
plan is valid only when its run config reflects the user's explicit semantic-QC choice.

### 2. Generate Reusable Dependencies

```bash
python3 <workflow-root>/scripts/run_mv_pipeline.py base-assets \
  --storyboard <storyboard.json> --audio <audio> --output <output> \
  --model-config <model-config.json> --config <run-config.json>
```

For Wan, this generates cast identity portraits through the configured image provider. For Seedance,
this records the configured Ark image asset IDs locally and makes no image-generation or asset-registration
calls. Wardrobe, scene, blocking, and action remain prompt fields.

Inspect generated identity portraits for Wan or the selected Ark character assets for Seedance before video generation, and verify every package's complete setting, layout, light, palette, and material description.

### 3. Review Video Packages

```bash
python3 <workflow-root>/scripts/run_mv_pipeline.py prepare-videos \
  --storyboard <storyboard.json> --audio <audio> --output <output> \
  --model-config <model-config.json> --config <run-config.json>
```

Review `reports/video_generation_packages.json`. Its `request_preview` shows the selected provider's payload shape: audio and Wan image URLs are non-routable placeholders, while Seedance character references contain the configured Ark image asset `asset://` IDs. This phase neither uploads audio nor submits a paid task. Verify that every package contains one shot, one full-duration window, the matching audio interval, identity-reference order, complete scene text, literal visual content, cast presence, wardrobe, actions, camera, lyrics, and storyboard-declared edit anchor.

### 4. Generate Segments and Optionally Review Them

Submit the complete storyboard shot set as one rate-limited batch. Poll all accepted provider tasks together and download each result as soon as it succeeds. When semantic QC is enabled, send every review-ready candidate to `omni_call` concurrently up to `quality_control.workers`, using the exact source-audio interval and [shot-quality-control.md](references/shot-quality-control.md). Omni returns one review for that editorial shot, rated `fully_compliant`, `minor_issues`, or `major_issues`. The local policy engine—not Omni—decides whether to accept or regenerate.

```bash
python3 <workflow-root>/scripts/run_mv_pipeline.py videos \
  --storyboard <storyboard.json> --audio <audio> --output <output> \
  --model-config <model-config.json> --config <run-config.json>
```

Semantic QC is disabled by default. Do not change `quality_control.enabled` to `true`, call
`omni_call` for shot review, invoke the standalone `quality-control` phase, or regenerate a shot from
a semantic review unless the user explicitly requested semantic QC or supplied a run config that
already enables it. This default adds no Omni shot-review calls, semantic-review waiting time, or
review-triggered video generation calls.

When enabled, the default strategy is `reject_major`: accept `fully_compliant` and `minor_issues`,
but regenerate the individual shot when it has `major_issues`. Keep at most
`quality_control.max_rounds=3` candidates in total. If all three are rejected, select the best
deterministically by categorical rating, then fewest explicit major and minor issue records; an exact
tie favors the earlier round. The selected fallback and its remaining problems stay explicit in the
QC report. `accept_all` records ratings but accepts the first candidate. The user may change the
strategy, maximum rounds, Omni model/sampling, rubric path, or extra project requirements in config.

If `quality_control.enabled=false`, skip Omni calls and semantic regeneration entirely; do not claim that generated content was semantically verified.

The scheduler preserves explicit submission, polling, download, and parallel review states:

1. submit every segment at the selected provider's configured RPM; isolated rate errors are requeued at the current speed, while a burst or high error ratio steps down through that provider's configured ladder
2. poll all submitted tasks
3. download each successful provider result immediately because result URLs may expire
4. review all eligible candidates concurrently up to `quality_control.workers`
5. submit every policy-rejected segment together as the next rate-limited generation round, rather than blocking the queue on one segment

A single transient `429` requeues only that task and does not slow the batch. Widespread rate errors trigger gradual slowdown. Submission, generation, download, QC candidates, QC decisions, and selected fallback states remain explicit. Execution never marks completion while a selected segment is missing or failed, and it stops loudly rather than skipping a task after retry limits are exhausted.

When semantic QC is enabled, review already-downloaded segments without otherwise running video
generation with:

```bash
python3 <workflow-root>/scripts/run_mv_pipeline.py quality-control ...
```

### 5. Assemble and Verify

```bash
python3 <workflow-root>/scripts/run_mv_pipeline.py assemble ...
python3 <workflow-root>/scripts/run_mv_pipeline.py verify ...
```

When semantic QC is enabled, assembly requires every shot to be `accepted` or `fallback_selected`.
The default assembler applies four-frame boundary softening (`boundary_blend_frames=4`, about 0.167
seconds at 24 fps) while preserving the total frame count and timeline. Set `boundary_blend_frames=0`
for exact hard cuts. The original complete audio is always the final soundtrack.

When `subtitles.enabled=true`, assembly resolves the accepted sentence-level lyrics, preserves a clean
master at `video_segments/assembled_with_audio.mp4`, and burns lyrics into the configured final MV
using [subtitles.md](references/subtitles.md). Pass `--lyrics-srt <accepted.srt>` to override project
auto-discovery. A validated `no_lyrics` result is recorded and produces an uncaptioned final file;
missing or malformed subtitle evidence stops rather than silently dropping subtitles.

Technical verification remains separate from Omni semantic QC. It proves media properties, not the correctness of the model's visual judgment.

### 6. Resume

Reuse the same output directory. Successful image assets and video segment tasks are reused only when their execution signature still matches the provider schema, endpoint, model, parameters, references, prompt, and duration where applicable. Changing provider invalidates the corresponding execution signature rather than reusing an incompatible result.

## One-Command Run

Use only after storyboard validation and execution compatibility. When the root Music-to-MV Skill has recorded explicit `final_mv` or `resume` authorization, run without a second approval prompt:

```bash
python3 <workflow-root>/scripts/run_mv_pipeline.py all \
  --storyboard <storyboard.json> --audio <audio> --output <output> \
  --model-config <model-config.json> --config <run-config.json>
```

## Output Contract

The output directory records reusable assets, prompts, payloads, task IDs, responses, URLs, downloads, every retained QC candidate, per-round Omni reports, the selected candidate and fallback disclosure, execution signatures, normalized segments, the clean assembled master, canonical SRT, rendered ASS, subtitle report, burned final MV, and technical QC reports.
