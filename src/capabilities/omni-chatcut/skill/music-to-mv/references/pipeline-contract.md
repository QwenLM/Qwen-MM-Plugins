# Pipeline Contract

## Ownership

| Concern | Internal owner |
|---|---|
| structure, lyrics, instruments, caption run state | music-caption workflow |
| media-form compatibility, global visual style, timed shots, prompts, transitions, storyboard JSON | storyboard workflow |
| identity assets, selected video-provider tasks, Omni shot QC, resume state, assembly, technical QC | video-generation workflow |
| state detection, routing, profiles, handoff paths | root Music-to-MV Skill |

## Valid handoffs

- Caption resume → caption: a `music2mv/music-caption-run/v1` state file, readable recorded source
  audio, and every reusable response or annotation referenced by that state file.
- Caption → storyboard: completed caption, optional normalized music map, source audio, user brief.
- Storyboard → execution: validator-approved storyboard JSON, source audio, output directory, image/video-provider config.
- Execution → completion: persistent state, generation manifest, semantic-QC report recording either
  the enabled decisions or the default skipped state, assembled MV, technical-QC report.

Never mutate a validated storyboard during execution. Return to the storyboard workflow when creative
or structural changes are required.

## Invalidation

```text
source audio changed       → caption, storyboard, execution stale
caption/music map changed  → storyboard, execution stale
material brief changed     → storyboard, execution stale
storyboard changed         → execution stale
image/video config changed → execution revalidation only
```

Artifact existence alone is insufficient: use the owning workflow's validator or completion contract.
Files under `work/music-caption/` are disposable and never prove caption completion; canonical caption
artifacts and resumable evidence live under `analysis/music-caption/`.
