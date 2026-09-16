# JSON Schemas

This document defines the JSON schemas used by skill-creator, plus the shape of the perception log (`video_events.md`).

---

## evals.json

Defines the evals for a skill. Located at `evals/evals.json` within the skill directory.

```json
{
  "skill_name": "example-skill",
  "eval_config": {
    "run_l3": true,
    "review_mode": "human-feedback",
    "max_iterations": 1
  },
  "evals": [
    {
      "id": 1,
      "prompt": "User's example prompt",
      "initial_state": "A fresh copy of the project is open",
      "isolation": "stateful_shared",
      "expected_output": "Description of expected result",
      "files": ["evals/files/sample1.pdf"],
      "expectations": [
        "The output includes X",
        "The skill used script Y"
      ]
    }
  ]
}
```

**Fields:**
- `skill_name`: Name matching the skill's frontmatter
- `evals[].id`: Unique integer identifier
- `evals[].prompt`: The task to execute
- `evals[].initial_state`: Initial application/project state required by the task, or `not applicable`
- `evals[].isolation`: `isolated` for independent headless work, or `stateful_shared` when runs touch a shared GUI, profile, project, account, or live MCP target
- `evals[].expected_output`: Human-readable description of success
- `evals[].files`: Optional list of input file paths (relative to skill root). Prefer material cropped from or recreated per the source video over synthesized stand-ins
- `evals[].expectations`: List of verifiable statements

**Optional top-level fields (Three-Layer Eval Framework):**
- `grader_selection` (object): records which trait overlays were stacked on the base grader and why —
  `traits` (list, `[]` when none) and `traits_reason` (string). The base grader is format-agnostic
  and derives its own evidence ladder, so there is no per-format scene to select or record
- `eval_config` (object): Eval behavior configuration
  - `run_l3` (boolean): Whether to run L3 downstream task eval. Default: `true`
  - `review_mode` (string): `human-feedback` by default; `agent-self-iterate` only when the caller explicitly says no human feedback is available and authorizes autonomous iteration
  - `max_iterations` (positive integer): L3 iteration cap. Default `1` in human-feedback mode and `2` in agent-self-iterate mode unless the caller explicitly chooses another cap

---

## grading.json

Output from the grader agent. Located at `<run-dir>/grading.json`.

```json
{
  "grading_provenance": {
    "mode": "independent_subagent",
    "blind_to_configuration": true
  },
  "expectations": [
    {
      "text": "The output includes the name 'John Smith'",
      "passed": true,
      "evidence": "Found in transcript Step 3: 'Extracted names: John Smith, Sarah Johnson'"
    },
    {
      "text": "The spreadsheet has a SUM formula in cell B10",
      "passed": false,
      "evidence": "No spreadsheet was created. The output was a text file."
    }
  ],
  "summary": {
    "passed": 2,
    "failed": 1,
    "total": 3,
    "pass_rate": 0.67
  },
  "execution_metrics": {
    "tool_calls": {
      "Read": 5,
      "Write": 2,
      "Bash": 8
    },
    "total_tool_calls": 15,
    "total_steps": 6,
    "errors_encountered": 0,
    "output_chars": 12450,
    "transcript_chars": 3200
  },
  "timing": {
    "executor_duration_seconds": 165.0,
    "grader_duration_seconds": 26.0,
    "total_duration_seconds": 191.0
  },
  "claims": [
    {
      "claim": "The form has 12 fillable fields",
      "type": "factual",
      "verified": true,
      "evidence": "Counted 12 fields in field_info.json"
    }
  ],
  "user_notes_summary": {
    "uncertainties": ["Used 2023 data, may be stale"],
    "needs_review": [],
    "workarounds": ["Fell back to text overlay for non-fillable fields"]
  },
  "eval_feedback": {
    "suggestions": [
      {
        "assertion": "The output includes the name 'John Smith'",
        "reason": "A hallucinated document that mentions the name would also pass"
      }
    ],
    "overall": "Assertions check presence but not correctness."
  }
}
```

**Fields:**
- `grading_provenance`: Required grading provenance. `mode` is `independent_subagent` or `inline_fallback`; `blind_to_configuration` records observed blindness, not an assumption. Missing provenance is UNKNOWN. Do not record grader task identifiers or model names.
- `expectations[]`: Graded expectations with evidence
- `summary`: Aggregate pass/fail counts
- `execution_metrics`: Tool usage and output size (from executor's metrics.json)
- `timing`: Wall clock timing (from timing.json)
- `claims`: Extracted and verified claims from the output
- `user_notes_summary`: Issues flagged by the executor
- `eval_feedback`: (optional) Improvement suggestions for the evals, only present when the grader identifies issues worth raising

---

## metrics.json

Output from the executor agent. Located at `<run-dir>/outputs/metrics.json`.

```json
{
  "tool_calls": {
    "Read": 5,
    "Write": 2,
    "Bash": 8,
    "Edit": 1,
    "Glob": 2,
    "Grep": 0
  },
  "total_tool_calls": 18,
  "total_steps": 6,
  "files_created": ["filled_form.pdf", "field_values.json"],
  "errors_encountered": 0,
  "output_chars": 12450,
  "transcript_chars": 3200
}
```

**Fields:**
- `tool_calls`: Count per tool type
- `total_tool_calls`: Sum of all tool calls
- `total_steps`: Number of major execution steps
- `files_created`: List of output files created
- `errors_encountered`: Number of errors during execution
- `output_chars`: Total character count of output files
- `transcript_chars`: Character count of transcript

---

## timing.json

Wall clock timing for a run. Located at `<run-dir>/timing.json`.

**How to capture:** When a subagent task completes, the task notification includes `total_tokens` and `duration_ms`. Save these immediately — they are not persisted anywhere else and cannot be recovered after the fact.

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332,
  "total_duration_seconds": 23.3,
  "executor_start": "2026-01-15T10:30:00Z",
  "executor_end": "2026-01-15T10:32:45Z",
  "executor_duration_seconds": 165.0,
  "grader_start": "2026-01-15T10:32:46Z",
  "grader_end": "2026-01-15T10:33:12Z",
  "grader_duration_seconds": 26.0
}
```

---

## reset.json

For an eval marked `stateful_shared`, record the reset performed before its executor starts. Keep the
file beside `timing.json` in the run directory.

```json
{
  "method": "Reopened a clean copy of project.blend",
  "result": "passed",
  "evidence": "Startup screenshot and project hash recorded before execution"
}
```

Use `result: "unknown"` when the clean state cannot be demonstrated; an absent or unsuccessful reset
must not be reported as clean isolation.

---

## iteration_metadata.json

Identifies the exact skill revision and review policy measured by one L3 iteration. Located at
`<run-tmp>/eval_run/iteration-<N>/iteration_metadata.json`.

```json
{
  "iteration": 1,
  "review_mode": "agent-self-iterate",
  "skill_revision": "sha256:...",
  "parent_iteration": null,
  "verification_scope": [0, 1, 2],
  "measurement_status": "complete",
  "promotion_status": "promoted"
}
```

**Fields:**
- `iteration`: Positive iteration number
- `review_mode`: `human-feedback` or `agent-self-iterate`
- `skill_revision`: Content hash of the snapshot executed in this iteration
- `parent_iteration`: Previous iteration number, or `null` for the first measured revision
- `verification_scope`: Eval IDs that must all complete for this revision
- `measurement_status`: `running`, `complete`, or `failed`; an iteration with a missing executor result, grading result, or benchmark is `failed`
- `promotion_status`: `candidate`, `promoted`, or `rejected`. Only a `complete` + `promoted` revision may be delivered

---

## benchmark.json

Output from `aggregate_benchmark.py`. Located at
`<run-tmp>/eval_run/iteration-<N>/benchmark.json`.

```json
{
  "metadata": {
    "skill_name": "pdf",
    "skill_path": "/path/to/pdf",
    "executor_model": "qwen3.5-omni-plus",
    "analyzer_model": "most-capable-model",
    "timestamp": "2026-01-15T10:30:00Z",
    "evals_run": [1, 2, 3],
    "runs_per_configuration": 1
  },

  "runs": [
    {
      "eval_id": 1,
      "eval_name": "Ocean",
      "configuration": "with_skill",
      "run_number": 1,
      "result": {
        "pass_rate": 0.85,
        "passed": 6,
        "failed": 1,
        "total": 7,
        "time_seconds": 42.5,
        "tokens": 3800,
        "tool_calls": 18,
        "errors": 0
      },
      "grading_provenance": {
        "mode": "independent_subagent",
        "blind_to_configuration": true
      },
      "expectations": [
        {"text": "...", "passed": true, "evidence": "..."}
      ],
      "notes": [
        "Used 2023 data, may be stale",
        "Fell back to text overlay for non-fillable fields"
      ]
    }
  ],

  "run_summary": {
    "with_skill": {
      "pass_rate": {"mean": 0.85, "repeat_stddev": null, "cross_eval_stddev": 0.14, "min": 0.65, "max": 1.0, "n_runs": 3, "n_evals": 3},
      "time_seconds": {"mean": 42.5, "repeat_stddev": null, "cross_eval_stddev": 8.2, "min": 34.0, "max": 50.0, "n_runs": 3, "n_evals": 3},
      "tokens": {"mean": null, "repeat_stddev": null, "cross_eval_stddev": null, "min": null, "max": null, "n_runs": 0, "n_evals": 0}
    }
  },

  "notes": [
    "Assertion 'Output is a PDF file' would pass without the skill too - non-discriminating",
    "Eval 3 ran once, so its 50% is a single observation, not a spread",
    "Two FAILs on eval 2 are missing-renderer cases and should have been NOT_VERIFIABLE"
  ]
}
```

**Fields:**
- `metadata`: Information about the benchmark run
  - `skill_name`: Name of the skill
  - `timestamp`: When the benchmark was run
  - `evals_run`: List of eval names or IDs
  - `runs_per_configuration`: Runs found per config per eval — the actual count, not an assumed one.
    Normally `1`; it is what says whether a spread is measurable at all
- `runs[]`: Individual run results
  - `eval_id`: Numeric eval identifier
  - `eval_name`: Human-readable eval name (used as section header in the benchmark report)
  - `configuration`: the config directory's name — normally `"with_skill"`, the only config L3 runs.
    The release workflow does not create another configuration; the script merely discovers generic
    config directory names so external harnesses can retain compatible archives
  - `run_number`: Integer run number (1, 2, 3...)
  - `result`: Nested object with `pass_rate`, `passed`, `total`, `time_seconds`, `tokens`, `errors`
  - `grading_provenance`: copied from `grading.json`; `null` means the independence evidence was not recorded, not that inline grading occurred
- `run_summary`: Statistical aggregates per configuration
  - one key per configuration (normally just `with_skill`): each contains `pass_rate`,
    `time_seconds`, and `tokens` objects. `repeat_stddev` measures repeat runs of the same eval;
    `cross_eval_stddev` measures dispersion across different eval means. A single run cannot estimate
    repeat variance, so `repeat_stddev` is `null`, not zero. Missing timing/token evidence likewise
    stays `null` rather than being synthesized from another field
  - `delta`: **absent with a single configuration** — there is nothing to subtract from, and a
    difference against an unrun baseline would read as a demonstrated improvement. Present only when
    two or more config directories exist, as difference strings like `"+0.50"`, `"+13.0"`, `"+1700"`
- `notes`: Freeform observations from the analyst pass (see `references/eval-and-iterate.md` Step 4)

**Important:** `aggregate_benchmark.py` writes these field names exactly, and the analyst pass reads them back out of `benchmark.json`. Using `config` instead of `configuration`, or putting `pass_rate` at the top level of a run instead of nested under `result`, will produce empty/zero values downstream. Always reference this schema when generating benchmark.json manually.

---

## Video Events (`video_events.md`)

The perception log, written to `.build/video_events.md`. Not a schema-checked artifact — it's a working document, created by the global pass and then edited in place as perception continues. In video-first, `read_native_av`'s pinned `event_log` prompt already emits this shape, so normally you keep what it returned and append to it; in audio-first, the blocks take this shape as watched stretches merge with the transcript (see `references/perceiver.md`).

One `###` block per coherent event, in time order:

```markdown
### [00:02:05.000-00:02:16.000] step — Duplicate the curved shape and picture-fill the top copy
- shown: user holds Ctrl+Shift and drags the shape left, creating an aligned duplicate; opens Format Shape on the top copy and switches the fill to Picture
- said: "还可以按住CTRL+SHIFT键平移拖动图形" / "然后将顶层的图形设置为图片填充"
- on_screen_text: `设置形状格式 > 填充 > 图片或纹理填充`
- highlights: Ctrl+Shift keeps the copy on the same horizontal line — that alignment is the whole point, so don't free-drag it
- asset: frame@00:02:11.000 — the fill panel's option names can't be conveyed in prose alone
```

### Heading

`### [<start>-<end>] <type> — <one-line summary>`

- Timestamps are **absolute** in the source video, always zero-padded `HH:MM:SS.mmm` (e.g. `[00:04:51.000-00:05:05.000]`) so every line sorts and diffs identically regardless of video length. Sub-range reads return absolute times too, so a zoom pass appends into the same timeline. Asset *filenames* use the separate filename-safe `MMmSSs` prefix documented below.
- `<type>` is one of: `step` (an action to reproduce) · `setup` (prerequisite / environment / materials) · `explanation` (a concept or the *why*) · `result` (an outcome shown) · `tip` (a preference / best practice) · `warning` (a pitfall / caveat) · `framing` (intro/outro/transition — no teachable content). Drafting sorts events by this, so keep it accurate.

### Lines

| Line | Carries | Notes |
|------|---------|-------|
| `shown` | The on-screen action and what changes on screen | Always present |
| `said` | The narration, quoted verbatim in the speaker's language | Omit for a silent stretch |
| `on_screen_text` | Text legible in frame: menu paths, labels, values, commands | Omit if none |
| `highlights` | The tacit expertise a bare summary loses — a stated preference, the *why* behind a choice, something stressed, a caveat dropped in passing | The highest-value line; drafting reads these as the skill's preferences, rationale and warnings |
| `asset` | `frame@HH:MM:SS.mmm` or `clip@HH:MM:SS.mmm-HH:MM:SS.mmm`, plus what a text-only account would lose | A candidate for extraction, not a commitment — the media_plan decides |

Free-form prose between blocks is fine (a `## Source` header with duration and sha256, a note about something that stayed illegible). Prefer refining an existing block over appending a duplicate for the same moment.

---

## Asset Manifest (`asset_manifest.json`)

The complete inventory of multimodal assets extracted for a skill. Lives at `skill/asset_manifest.json`. Validated by `validate_skill` L1 checks.

```json
{
  "video_source": {
    "path": "original_video.mp4",
    "sha256": "a1b2c3...",
    "duration_sec": 120.5
  },
  "assets": [
    {
      "id": "frame_05m12s_node-menu",
      "type": "frame",
      "path": "assets/frames/05m12s_node-menu.png",
      "when_to_use": "When the consumer needs to see the node context menu layout",
      "text_description": "Screenshot of the Add Node context menu showing Math > Multiply",
      "timestamp": 312.0,
      "end_timestamp": null,
      "necessity_rationale": "The panel's arrangement — which control sits where, so a consumer can find it on sight"
    }
  ]
}
```

### `video_source` fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | Path to the source video |
| `sha256` | string | yes | SHA-256 hash of the source video |
| `duration_sec` | number | yes | Video duration in seconds |

### `assets[]` entry fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | **yes** | Unique asset identifier |
| `type` | enum | **yes** | `frame`, `clip`, `audio` (taken from the video) or `generated` (produced by code/a render) |
| `path` | string | **yes** | Relative path to the asset file |
| `when_to_use` | string | **yes** | When the skill consumer should reference this asset |
| `text_description` | string | **yes** | What the asset shows/contains |
| `timestamp` | number | **yes for `frame`/`clip`/`audio`** | Source time in seconds; must match the `MMmSSs` filename prefix |
| `end_timestamp` | number | no | End timestamp in seconds (clips/audio) |
| `necessity_rationale` | string | no | The perceptual thing this asset carries — what a consumer has to recognise, match or reproduce |

**Required fields** (enforced by `validate_skill` L1): `id`, `type`, `path`, `when_to_use`, `text_description`.

### Provenance (`validate_skill` #30)

The `MMmSSs` prefix is a *claim* that the asset came out of the video at that time, so it must be
backed by a matching `timestamp`:

- `frame` / `clip` / `audio` — must carry a numeric `timestamp`, consistent with the filename
  prefix (1s tolerance). A missing or contradictory timestamp fails validation.
- `generated` — for anything your own code or a render produced (e.g. a gallery built from the
  skill's bundled helpers). It must **not** use the `MMmSSs` prefix, so it can never be mistaken
  for a real frame. Use this instead of relabelling a rendered image as a `frame`.

### Naming conventions

- Frames: `MMmSSs[_label].png` (e.g. `05m12s_node-menu.png`)
- Clips: `MMmSSs_MMmSSs.mp4` (e.g. `05m12s_05m24s.mp4`)
- Audio: `MMmSSs_MMmSSs.mp3` (e.g. `05m12s_05m24s.mp3`)
- Generated: any descriptive name **without** a leading `MMmSSs` (e.g. `six-techniques-gallery.png`)

---

## L2 Pre-check Output

L2 asset pre-checks produce advisory findings (WARNING level, never block L3).

### Rule-based checks (#25-#27) — via `validate_skill`

Checks #25 (necessity_rationale completeness), #26 (index-manifest consistency), and #27 (orphan asset detection) run as part of `validate_skill` and appear in its standard return structure:

```json
{
  "status": "pass",
  "errors": [],
  "warnings": [
    "#25 Asset `frame-002` missing or empty `necessity_rationale`.",
    "#26 Assets in manifest but not in index: clip-003.",
    "#27 Asset `frame-005` is in manifest but not referenced in SKILL.md body."
  ],
  "summary": { "assets_checked": 5 }
}
```

### Dedup checks (#23-#24) — via MCP tools

Check #23 (image dedup) uses `dedup_frames`; check #24 (audio dedup) uses `dedup_audio`. Each returns a JSON result:

```json
{
  "unique": [
    {"index": 0, "segment": [10.0, 10.0]}
  ],
  "duplicates": [
    {"index": 1, "segment": [12.0, 12.0], "duplicate_of": 0, "distance": 3}
  ]
}
```

The orchestrator collects these results and reports duplicates as WARNINGs alongside the `validate_skill` output.
