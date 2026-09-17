<!-- Internal workflow: loaded by the root Omni ChatCut Movie Commentary Skill. -->
# Source Analysis Workflow

Produce complete, auditable source evidence before narration is authored.

## Preflight and facts

Call `prepare_movie_commentary_project` for a new project. Inspect frames near the ending and dialogue
regions, then complete `plan/execution_facts.json` with measured `credits_start_sec`, conservative
`source_cut_max_sec`, burned-subtitle band (or explicit absence), output canvas/aspect policy, one verified
commentary subtitle style/font, and audio defaults. Do not guess credits as duration minus a constant.

## Complete-film evidence

Cover source time `[0,duration]` with chronological clips, normally 5–8 minutes with 5–10 seconds overlap.
Create every clip under `work/analysis_clips/`; call the bundled `omni_call` with a neutral factual prompt
requiring storyline, visible text, speakers/transcript, and timestamps. The Omni tool consumes the supplied
file, so map every clip-relative timestamp back to absolute source time. Never pass the entire feature when
the intended evidence interval is one chunk.

Persist for each accepted leaf interval:

```text
plan/watch_notes/chunk_NNN_raw.md   # unedited Omni response
plan/watch_notes/chunk_NNN.md       # interval, clip, model mode, concise facts, identities, continuity
```

On failure, subdivide only the failed interval. Keep successful neighbors and record rejected parent calls.
Coverage is complete only when ordered accepted leaves cover the whole source without a gap. Use a separate
short clip and `omni_call` when timestamped dialogue or a critical identity needs verification. Do not use
web knowledge, a sibling movie-commentary capability, or Video Memory to fill evidence gaps.

Set project analysis state complete only after facts and coverage pass. Analysis-only stops here.
