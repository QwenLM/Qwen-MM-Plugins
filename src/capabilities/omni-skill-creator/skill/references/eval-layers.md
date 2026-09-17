# Three-Layer Eval Verification Framework

Reference document for the orchestrator and grader agents. Read this when you need to understand how eval verification is structured.

## Layers

| Layer | Name | Executor | Cost | Core Question | Scope |
|-------|------|----------|------|---------------|-------|
| **L1** | Structural Validation | `validate_skill` MCP tool | Zero (Python) | Files exist? Names correct? Links resolve? Schema valid? | Universal |
| **L2** | Asset Pre-check | `validate_skill` checks #25-#27 + `dedup_frames`/`dedup_audio` MCP tools | Zero/Low | Duplicate assets? Orphan assets? Missing rationale? | Universal |
| **L3** | Downstream Task Eval | executor + grader agents | Moderate (N executor + N grader calls per iteration) | Can an agent complete the task using this skill? | **Task-specific** |

## Gate Logic

```
L1 (validate_skill) ──── must pass ────→ L2 (asset pre-checks)
                                          │
                                    has assets → run
                                    no assets  → skip
                                          │
                                          ▼
                              eval_config.run_l3 check
                                    │          │
                                false → stop    true → choose review_mode
                                report L1+L2              │
                                               human-feedback / agent-self-iterate
                                                         │
                                                         ▼
                                               L3 iteration-N (single arm)
                                               spawn + grade + aggregate
                                                         │
                                             feedback/evidence → proposal
                                                         │
                                              verified next iteration or stop
```

## L2 Checks

| # | Check | Type | Implementation |
|---|-------|------|----------------|
| 23 | Image dedup (dHash) | Perceptual hash | `dedup_frames` MCP tool |
| 24 | Audio dedup (MFCC) | Audio fingerprint | `dedup_audio` MCP tool |
| 25 | `necessity_rationale` completeness | Rule | `validate_skill` check |
| 26 | index-manifest consistency | Rule | `validate_skill` check |
| 27 | Orphan asset detection (SKILL.md refs) | Rule | `validate_skill` check |
| 28 | Asset over-length (minimal-sufficient) | Rule (span from name) | `validate_skill` check |

L2 findings are **advisory** (WARNING level) — they do not block L3. Check #28 nudges toward minimal-sufficient assets now that `extract_clip`/`extract_audio_clip` no longer hard-cap clip length: soft thresholds (clip > 15 s, audio > 30 s) raise a WARNING to reconsider trimming, but never block.

## L3 Grader Overlays

The grader prompt is composed from the base grader plus whichever trait overlays apply:
```
grader prompt = Read(agents/grader.md)
              + Read(agents/grader-trait-<name>.md) …    # zero or more
```

The base grader is format-agnostic: instead of selecting a per-format scene, it derives an evidence ladder for whatever artifact it is handed (what kind of thing is this → what can this environment do to it → what is the strongest evidence available). One grader covers every domain, and an unfamiliar one degrades gently by construction rather than falling off a list of known formats.

Trait overlays are discovered, not listed. They live in `agents/`:

- `grader-trait-<name>.md` — a concern that cuts across artifact families. `animation` and `audio` exist; `cjk-text` is an obvious next one and is not written yet. Zero or more apply and they stack, so a caveat that holds for several artifact families alike is written once instead of once per family.

Each declares in front-matter what it `applies_to` (output extensions, keywords). Selection reads those declarations, records the choice in `evals.json` under `grader_selection`, and adding a caveat means adding a file, and nothing else.

Two rules the base grader carries, both learned from measured misses. **A project file outranks its export**: when a `.blend`, `.FCStd`, `.drawio` or `.kicad_pcb` ships next to a render, the project file is the real artifact, because the export has discarded the structure the lesson was about — grade the project file's structure, not the render. And **traits are stacked, not optional** — one 47-run batch shipped `.mp3` and `.mid` artifacts with `grader-trait-audio` selected zero times, so the ladder that would have measured them was never read.

Keep trait overlays thin. They carry what the model cannot work out for itself — fidelity traps, dead ends, and what a bare model can already do without any skill. They are deliberately *not* checklists of things to verify: a checklist becomes the ceiling of what gets checked, and what is worth checking depends on the artifact in front of you.

## L3 On/Off Switch

`evals/evals.json` optional field:
```json
{
  "eval_config": {
    "run_l3": true,
    "review_mode": "human-feedback",
    "max_iterations": 1
  }
}
```
- Missing `eval_config` or missing `run_l3` → defaults to `true` (backward compatible)
- `run_l3: false` → stop after L1+L2, report results
- `review_mode` → `human-feedback` by default; `agent-self-iterate` only when explicitly selected
- `max_iterations` → defaults to `1` for human-feedback and `2` for agent-self-iterate

L3 measures by *performing* the task, so it is the wrong instrument when performing it has real
consequences — spending, sending, booking, mutating a live system, or acting under an account that
isn't the run's to use. One run per eval is still one real action per eval, and N evals mean N of
them. Turn L3 off in those cases and say so; the skill ships `source-grounded`, carrying its own
verification steps for whoever runs it first.

## L3 Shape

Each iteration has one configuration (`with_skill`) and one run per active eval. That answers "can
an agent do this task with this skill" and deliberately does not answer "how much does the skill
add", which would require a without-skill measurement L3 never makes. Human-feedback mode runs one
iteration and waits for human feedback; explicitly selected `agent-self-iterate` mode uses archived
grader and artifact evidence as feedback and may run further iterations within its cap. Every
iteration has its own skill snapshot and benchmark, and an edit cannot replace the last verified
revision until all active evals rerun against it. See `references/eval-and-iterate.md`.
