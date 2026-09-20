<!-- Internal workflow: loaded by the root qwen-mm-plugins-omni-chatcut-music-to-mv Skill; not independently discoverable. -->

# Music Caption Workflow

Produce one complete caption from a local music file. Persist resumable evidence inside the Music2MV
project while keeping temporary audio clips separate; return only the final caption unless the user
asks for diagnostics.

## Required tools

- `omni_call`: this capability's Qwen Omni MCP tool; reads its endpoint, model, and credential
  environment-variable name from the shared model-connection config.
- `slice_audio_from_structure`: this capability's deterministic ffmpeg slicing MCP tool.

Do not substitute `omni_music_caption` for the custom prompts below. It has a different output
contract. Do not claim any Omni call is local inference.

Before a live call, follow `../../references/model-configuration.md`. Either set
`QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG` once through the shared Qwen-MM-Plugins configuration or pass
the same `model_config_path` to every
`omni_call`. Do not copy endpoint or credential settings into individual caption prompts or run
evidence.

## Workflow

### 1. Validate input and prepare prompts

Confirm one readable local audio file. Read these references completely before calling tools:

- `references/music_structure_asr_prompt.md`
- `references/sentence_lyrics_srt_prompt.md`
- `references/global_music_caption_prompt.md`
- `references/segment_vocal_instrument_prompt.md`

When a Music2MV project directory is available, create a unique `<run-id>` and use these locations:

```text
<project-root>/
  analysis/music-caption/
    caption.md
    evidence.json
    slice-manifest.json
    runs/<run-id>/
      run-state.json
      raw/
  work/music-caption/<run-id>/
    clips/
```

The `analysis/` files are durable project artifacts. The versioned run directory preserves raw Omni
responses, retry parameters and errors, the exact validated evidence object, the returned slice
manifest, and the completed caption for audit or resume. The `work/` directory contains disposable
WAV clips and may be removed only after successful synthesis and after the durable artifacts have
been written.

Initialize `run-state.json` with schema `music2mv/music-caption-run/v1`, the project-relative run and
work directories, the source-audio path, `status: "running"`, and `phase: "prepared"`. Update `phase`
after each successful stage and retain the last error when a stage fails. Do not store credentials or
inline audio in this state file.

```json
{
  "schema": "music2mv/music-caption-run/v1",
  "run_id": "<run-id>",
  "run_dir": "analysis/music-caption/runs/<run-id>",
  "work_dir": "work/music-caption/<run-id>",
  "source_audio": "inputs/<source-audio>",
  "status": "running|failed|complete",
  "phase": "prepared|first_wave_complete|sliced|clips_annotated|synthesized",
  "branches": {},
  "segments": {},
  "last_error": ""
}
```

When resuming, verify every recorded file before reusing it and continue from the first incomplete
phase. Reuse valid saved Omni responses and clip annotations instead of repeating paid calls. If
durable structure evidence exists but disposable clips have been removed, recreate the clips from
the saved structure response rather than rerunning the structure analysis.

For a one-off analysis with no project directory, create a unique directory under
`/tmp/music-caption-for-mv/` and use the same internal layout. Treat that run as ephemeral: do not
claim it is resumable, and do not add it to a project manifest later unless its durable artifacts are
first copied into that project. Never overwrite a previous run or write clips beside the source
audio.

### 2. Run independent first-wave analysis concurrently

If the harness supports parallel tool calls, dispatch all three calls together:

1. Call `omni_call` on the complete audio with the unchanged structure/ASR prompt:
   - `output_format="text"`
   - `temperature=0.01`
   - `max_tokens=16384`
2. Call `omni_call` on the complete audio with the unchanged sentence-level SRT prompt:
   - `output_format="text"`
   - `temperature=0.01`
   - `max_tokens=16384`
3. Call `omni_call` on the complete audio with the unchanged global-caption prompt:
   - `output_format="text"`
   - `temperature=0.01`
   - `max_tokens=4096`
These calls are independent. Do not wait for one before starting another when parallel dispatch is
available. A failed branch must be reported and retried or resolved; do not fabricate its output.
For project-backed runs, save each response immediately under the run's `raw/` directory and record
the successful branches plus their parameters in `run-state.json`; do not wait until final synthesis
to make first-wave evidence durable.

### 3. Validate structure and cut audio

Validate that every non-empty structure/ASR line consistently uses exactly one of these forms:

```text
[MM:SS,mmm --> MM:SS,mmm] [lowercase-label] "lyrics"
[HH:MM:SS,mmm --> HH:MM:SS,mmm] [lowercase-label] "lyrics"
```

Treat the parseable line shape and timeline as the structure branch's hard acceptance criteria.
Prefer one timestamp form, but pass the response to `slice_audio_from_structure` before rejecting it.
The slicer accepts the unambiguous Omni drift seen in practice: mixed `MM:SS,mmm` and
`HH:MM:SS,mmm` fields, plus a leading-zero three-digit seconds spelling such as `00:019,840` for
19.840 seconds. It canonicalizes every timestamp in such a response to `HH:MM:SS,mmm`. This
compatibility applies only when the numeric time is unambiguous; it must not repair or excuse a
broken timeline. Require positive-duration intervals that are seamless, strictly increasing,
non-overlapping, and start at zero. Do not reject or reroll solely because a detected section is
shorter than one second. The slicer merges such micro-sections into an adjacent analysis clip,
preserves their original boundary/label/lyrics in `merged_from`, and records a
`short_segment_merged` entry in `structure_anomalies`. Compare the last endpoint with
the real audio duration when media metadata is available. Treat the physical audio end as a hard
boundary: if only the predicted final endpoint exceeds it, let `slice_audio_from_structure` clamp that
endpoint to the probed duration and use the returned manifest boundary for every downstream step. Do
not analyze, describe, or retain a timeline beyond the real audio end. If the final segment starts at
or after the audio end, treat the structure result as invalid and retry rather than dropping segments.

Accept a structure result once these timestamp and slicing requirements pass even when its semantic
content does not follow every prompt preference. In particular, text such as `[Instrumental]` inside
the quoted lyrics field, imperfect lyric transcription, or debatable section labels must not by
itself trigger another structure rollout. Preserve such content verbatim as model output; do not
silently rewrite it. The separate sentence-level SRT branch remains the authoritative source for
lyrics in the final caption.

Preserve every raw Omni response for diagnostics. When the line shape cannot be compatibly parsed or
the normalized timeline is invalid, make up to three structure rollouts total (the initial call plus
at most two retries) with the same audio and
unchanged base prompt. A retry may append a concise format-error reminder and make a small, deliberate
adjustment to supported sampling parameters: lower `temperature` toward `0.0` for malformed or mixed
timestamp syntax; raise it only modestly, never above `0.1`, when an invalid timeline is repeated and
an alternative segmentation is needed. Increase `max_tokens` only when output was truncated. Stop at
the first valid timeline. Record the parameters and validation error for each attempt. Never weaken
the timestamp or slicing validation, silently repair semantic boundaries, or exceed the rollout cap.

Call `slice_audio_from_structure` with the original audio, raw validated structure text,
`<project-root>/work/music-caption/<run-id>/clips/` for a project-backed run (or the ephemeral run's
`work/music-caption/<run-id>/clips/` directory), `output_format="wav"`, and `overwrite=false`. Save the
exact returned manifest in the versioned run directory and use it as the sole mapping among
timestamps, labels, lyrics, and clip paths.
Carry `structure_anomalies` into the evidence object and final caption as a compact diagnostic note;
an automatic short-segment merge is not a caption failure.
When `timeline_clipped_to_audio_end=true`, treat the manifest's corrected final `end_timestamp` as
authoritative for clip analysis and final synthesis; retain `original_end_timestamp` only as diagnostic
metadata.

Validate the sentence-level lyric result independently. Accept either the exact string `NO_LYRICS`
or standard SRT blocks whose indices start at 1 and increase consecutively, whose timestamps strictly
match `HH:MM:SS,mmm --> HH:MM:SS,mmm`, whose start precedes end, and whose cues are chronological and
non-overlapping. Allow one or two non-empty lyric lines per cue and require a blank line between cues.
Treat SRT syntax and timeline validity as the hard acceptance criteria. A descriptive cue such as
`[Instrumental]`, a structure-like word, or imperfect lyric text may violate the prompt preference,
but must not by itself trigger another rollout; preserve accepted cue text verbatim. Retry only when
SRT syntax, numbering, timestamp format, ordering, overlap, or media-duration bounds are invalid, with
up to three SRT rollouts total and the same bounded sampling policy as the structure call. After the
slicer returns `audio_duration_sec`, clamp only a final cue's end to the actual audio end when its
start is still inside the audio; retry if any cue starts at or beyond the audio end. Preserve the raw
and, when clamped, corrected SRT for diagnostics.

If `NO_LYRICS` conflicts with clearly transcribed lyrics in the structure/ASR result, retry the SRT
branch once. If the conflict remains, stop instead of silently choosing or fabricating lyrics.

### 4. Analyze independent clips concurrently

Call `omni_call` once for every returned `clip_path`, passing the unchanged segment
vocal/instrument prompt with:

- `output_format="text"`
- `temperature=0.01`
- `max_tokens=4096`

Clip calls are mutually independent. Dispatch them concurrently in bounded batches of at most four
unless the user or service configuration provides a lower limit. Keep results associated by segment
`index`; never rely on completion order. On 429/rate-limit errors, reduce concurrency and retry with
backoff. Do not omit a segment silently. For project-backed runs, persist each successful clip
annotation under `runs/<run-id>/raw/` as it completes and update the run state by segment index.

### 5. Synthesize with the calling model

Read `references/music_caption_evidence_contract.md` completely. Normalize the successful branches
into exactly one `music-caption-evidence` JSON object and run every hard validation in that contract.
Keep raw responses only as diagnostic evidence outside the object. Do not begin synthesis from loose
tool results, and do not continue with a missing, duplicated, mismatched, or invalid field.

For a project-backed run, save the validated object as `runs/<run-id>/evidence.json` before
synthesis. After the caption is complete, save `runs/<run-id>/caption.md`, then atomically update the
canonical `analysis/music-caption/evidence.json`, `slice-manifest.json`, and `caption.md`. When
`sentence_lyrics.status=ok`, also copy the validated SRT verbatim to
`analysis/music-caption/lyrics.srt`; for `no_lyrics`, do not create an empty SRT. Record those paths
in `music2mv-manifest.json` (including `artifacts.lyrics_srt` when present), mark the caption stage complete, and set the run state to
`status: "complete"`, `phase: "synthesized"`. Only then may the disposable clip directory be cleaned.

Then read `references/final_caption_synthesis.md` completely. The calling/main model—not another API
caption call—must consume the validated evidence object as its sole synthesis input.

Follow the reference's evidence boundaries and required output exactly. In particular:

- preserve structure and sentence lyrics as independent timelines; never assign SRT cues to
  structure intervals or print lyrics inside the Structure Timeline;
- preserve every sentence-level SRT cue's timestamp and text verbatim in the separate Lyrics
  Timeline, except an explicitly clamped final end timestamp;
- do not add any fact absent from the inputs;
- consolidate stable whole-track facts in the Overview and make each structure caption emphasize
  supported local changes while retaining continuing features needed to understand that section;
- retain distinct, useful local evidence and within-section development as specified in the synthesis
  reference; remove repetitive wording without dropping information dimensions merely for brevity,
  and do not require every per-clip field or invent missing details;
- keep local annotations inside their own timestamps;

Return only the completed caption. Expose intermediate results only when the user requests debugging,
auditing, or saved artifacts. Saving required project state is not additional user-facing output.

## Failure boundaries

- Stop rather than synthesize an incomplete caption if structure/ASR, sentence-level SRT lyrics,
  global caption, slicing, or any required clip annotation remains unavailable after
  reasonable retry.
- Do not derive lyrics from a global or clip caption, and do not merge structure-level ASR text into
  sentence-level SRT cues.
- Do not merge structure sections merely to shorten output. Keep every interval's local evidence,
  but keep the independent lyric timeline separate.
- Treat Omni timestamps, lyrics, and structure labels as model estimates, not ground truth.
- For a project-backed run that stops before completion, retain its run state and durable evidence,
  set `status: "failed"` with the last completed phase and concise error, and leave the canonical
  completed caption untouched.
