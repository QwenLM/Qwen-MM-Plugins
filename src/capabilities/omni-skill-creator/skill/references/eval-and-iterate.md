# Eval and iterate

Read this when `eval_config.run_l3` is `true` (the default). L3 is single-arm, not necessarily
single-iteration: each measured iteration runs every active test case once with the skill, grades
what came out, and derives one improvement proposal. It never adds a without-skill configuration.

So be precise about what L3 now claims: **can an agent do this task with this skill, to the standard
we wrote down** — not *how much the skill adds*. Two consequences, because the rest of this file
leans on them:

- **Nothing establishes that an eval was hard.** An assertion a bare model would satisfy anyway is
  reported as a pass exactly like one the skill earned, and nothing in the run separates them. That
  burden moves entirely onto how the assertions are written (Step 2).
- **A post-run edit is unverified until the next iteration finishes.** Keep the last fully measured
  skill revision as the deliverable. Put evidence-backed edits in `proposed_changes.md`, apply them
  only when another iteration can run, and promote the changed revision only after all active evals
  rerun against it. This keeps every measurement attached to the text that actually produced it.

## Review modes

Choose once before L3 and record the result in `eval_config.review_mode` and every
`iteration_metadata.json`:

- `human-feedback` (default) — present each measured iteration to the user and wait for actual
  feedback before changing the skill. Silence is not feedback until the user has had an opportunity
  to review.
- `agent-self-iterate` — use only when the caller explicitly says no human feedback is available and
  authorizes autonomous iteration. Do not wait for or invent human feedback. Treat the output
  artifacts, `grading.json`, executor `user_notes.md`, `benchmark.json`, and `analysis.md` as the
  feedback signal.

Human-feedback mode defaults to one iteration and continues only when the user asks. Agent-self-iterate mode
defaults to at most two iterations; an explicit positive `eval_config.max_iterations` or caller
instruction may set a different cap. The cap is an upper bound, not a target: stop early when the
evidence supports no skill change.

## Running and evaluating test cases

This section is one continuous sequence — don't stop partway through. Do NOT use `/skill-test` or any other testing skill.

Everything goes under `<run-tmp>/eval_run/`. The caller pins `<run-tmp>`; take it verbatim, and don't append a directory of your own naming or put evals beside the skill.

```
<run-tmp>/eval_run/
├── skill_snapshots/
│   ├── iteration-1/                              # exact skill measured in iteration 1
│   └── iteration-2/                              # exact changed revision measured next
├── iteration-1/
│   ├── iteration_metadata.json                   # review mode, revision, scope, status
│   ├── eval-<ID>-<descriptive-name>/             # eval-0-clean-cover-auto-english
│   │   ├── eval_metadata.json                    # task, expectations, initial state, isolation
│   │   └── with_skill/run-1/
│   │       ├── outputs/                          # ← what the subagent is told to write
│   │       ├── grading.json
│   │       ├── reset.json                        # for stateful_shared runs
│   │       └── timing.json
│   ├── benchmark.json / benchmark.md             # written by aggregate_benchmark
│   ├── analysis.md
│   └── proposed_changes.md
└── iteration-2/                                  # same shape, only when another round runs
```

Run `aggregate_benchmark.py` on one `iteration-<N>/` directory at a time. The
`with_skill/run-1/` nesting looks like ceremony when there is only one configuration and one run,
but the aggregator walks *eval dir → config dir → `run-*`* and **skips any config directory with no
`run-*` child**. The release workflow creates no second configuration; the generic directory level
keeps the archive format extensible. Never put two skill revisions under `run-1` and `run-2` of one
configuration — those are not statistical repeats.

Before each iteration, snapshot the exact skill about to be measured under
`<run-tmp>/eval_run/skill_snapshots/iteration-<N>/`, compute its content hash, and write that hash to
`iteration_metadata.json`. Do not edit the live skill again until the iteration reaches a terminal
status. Scratch that is not a deliverable goes under `<run-tmp>/scratch/`, outside the iteration archive.

### Step 1: Plan isolation, then run one execution per test case

For each test case, spawn one subagent with the skill. First classify the run:

- `isolated`: independent headless workspace and no shared application, profile, project, or live MCP target. These runs may launch concurrently.
- `stateful_shared`: controls a shared GUI, application profile, project, account, or live MCP target. Run these serially. Restore the declared initial state before each run and save the reset method, result, and evidence to `<run-dir>/reset.json`.

Concurrency is useful only when the runs cannot alter each other's evidence. A successful reset is part of the measurement; if it cannot be demonstrated, mark the initial state `UNKNOWN` instead of assuming it was clean.

```
Execute this task:
- Skill path: <path-to-skill>
- Task: <self-contained eval prompt with concrete target requirements>
- Input files: <eval files if any, or "none">
- Initial state: <relevant application/project state, or "not applicable">
- Save outputs to: <run-tmp>/eval_run/iteration-<N>/eval-<ID>-<name>/with_skill/run-1/outputs/
- Outputs to save: <what the user cares about — e.g., "the .docx file", "the final CSV">
- Also write <run-dir>/user_notes.md: what you consulted, what you tried that failed (and how many times), what you had to invent, what you couldn't verify. Facts only, ≤25 lines.
```

Point the subagent at the skill **on disk**. Inlining a condensed copy into the prompt tests a paraphrase you wrote, not what you ship.

Write an `iteration_metadata.json` at the iteration root before launching executors, then write an
`eval_metadata.json` at each `eval-<ID>-<name>/` level (expectations can be empty for now). Give each
eval a descriptive name based on what it tests — that name is both the `eval_name` field and the
directory suffix, so `eval-0` becomes `eval-0-clean-cover-auto-english`.

```json
{
  "iteration": 1,
  "review_mode": "human-feedback",
  "skill_revision": "sha256:...",
  "parent_iteration": null,
  "verification_scope": [0, 1, 2],
  "measurement_status": "running",
  "promotion_status": "candidate"
}
```

Set `measurement_status` to `complete` only after every eval in `verification_scope` has an executor
result, grading result, and the iteration benchmark; a crashed or incomplete round is `failed`.
Set `promotion_status` to `promoted` for the first complete iteration, and for a later iteration only
when the proposed defect improved without a new evidence-backed regression. Otherwise set it to
`rejected`. Only a `complete` + `promoted` revision may be delivered.

```json
{
  "eval_id": 0,
  "eval_name": "descriptive-name-here",
  "prompt": "The user's task prompt",
  "initial_state": "not applicable",
  "isolation": "isolated",
  "expectations": []
}
```

The prompt and inputs must stand on their own. The executor should not need the teaching video to discover what result was requested; the skill supplies the demonstrated method, not a hidden task definition.

### Step 2: While runs are in progress, draft assertions

Don't just wait for the runs to finish — use this time productively. Draft quantitative assertions
for each test case. If assertions already exist in `evals/evals.json`, review them. In human-feedback
mode, explain what they check to the user; in agent-self-iterate mode, record the same rationale in the
iteration's `analysis.md` inputs rather than inventing a conversation.

**Assertions carry the whole weight of discrimination here.** Nothing else demonstrates that an assertion was hard, so one a bare model would satisfy anyway reads exactly like one the skill earned. A capable model already knows the vocabulary of most domains and will produce plausible, well-formed output unprompted; what it does not know is the presenter's *specific* chain, specific values, specific ordering, and the reasons behind them. Assert those. "The file exists", "the output is valid JSON", "the answer mentions compression" — all pass without the skill and buy nothing.

Beyond that, aim high: assert what the artifact should *be*, and when the artifact has a visual or audible form **and this environment can actually produce it**, assert at least once on how it looks or sounds, since that is what such work is finally judged on. Getting that evidence may need a tool this environment lacks, so say in the assertion what to fall back to and what it means if neither path works (`agents/grader.md` covers the verdicts). Sometimes the demonstrated workflow cannot run here at all — a GUI product this sandbox doesn't have, or something needing an account, a login or network the run wasn't given; then assert not the artifact but whether the answer **faithfully reproduces the demonstrated method**, judged against `.build/video_events.md` (steps and their order, menu paths and values, the caveats the presenter raised). Take that route only after trying to produce the artifact and recording what stopped you — "produce a plan instead" is the easy way out, and it makes the eval much weaker when the artifact *was* possible. Subjective skills (writing style, design quality) are better evaluated qualitatively — don't force assertions onto things that need human judgment.

Update the `eval_metadata.json` files and `evals/evals.json` with the assertions once drafted — the field is called `expectations` in both, and in `grading.json`, because that is what the aggregation script reads; "assertion" is just what we call them in prose — `evals/evals.json` ships with the skill, so assertions that land only in `eval_metadata.json` are lost to whoever picks it up next. In human-feedback mode, also explain what you will show the user — both the qualitative outputs and the quantitative benchmark.

**If the skill ships assets, assert on one** — nothing else here tells you whether an asset carried anything. Assert what only holds if it did: a detail legible only in that asset coming out right in the output.

### Step 3: As runs complete, capture timing data

When each subagent task completes, you receive a notification containing `total_tokens` and `duration_ms`. Save this data immediately to `timing.json` beside that run's `outputs/` — i.e. `iteration-<N>/eval-<ID>-<name>/with_skill/run-1/timing.json`:

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332,
  "total_duration_seconds": 23.3
}
```

This is the only opportunity to capture this data — it comes through the task notification and isn't persisted elsewhere. Process each notification as it arrives rather than trying to batch them.

### Step 4: Grade, aggregate, and review results

Once all runs are done:

1. **Grade each run with a separate grader subagent** — the executor and the orchestrator that authored the skill must not grade their own output when subagents are available. Give the grader the original self-contained task, expectations, input files, execution transcript or notes, outputs, and `agents/grader.md` concatenated with the trait overlays that apply. Glob `agents/grader-trait-*.md`, match their `applies_to` front-matter against this eval's expected artifact, and take every trait that hits. The base grader is format-agnostic — it derives its own evidence ladder for whatever artifact it is handed, so there is no per-format scene overlay to select.

   Do not describe which configuration the grader is judging or include the tested skill path in its prose prompt; use an opaque output alias when practical. Record whether this blindness actually held rather than assuming it did. Inline grading is only a fallback when the harness has no subagent facility: use `grading_provenance.mode = "inline_fallback"`, set blindness honestly, and treat the result as qualitative evidence rather than an independent quantitative grade. An independent result uses `mode = "independent_subagent"`. Missing provenance is UNKNOWN, not implicit independence.

   Traits are stacked, not optional: check every trait against the artifact. When both a project file and its rendered preview are present, remember the project file is the real artifact — a `.blend` shipped beside a render PNG is a 3-D result, not an edited image; the base grader's ladder covers this, but note it in the selection reason.

   Record the choice in `evals/evals.json` under a `grader_selection` object with **exactly these two keys, both required**: `traits` (`[]` when none hit) and `traits_reason` (`""` when `traits` is empty). A reader looking for one spelling of a missing key finds nothing and reports a clean zero — that is why the names are fixed.

   Evaluate each assertion against the outputs. Save the result to `grading.json` beside that run's `outputs/` (`iteration-<N>/eval-<ID>-<name>/with_skill/run-1/grading.json`). Its `expectations` array must use **exactly** the fields `text`, `passed`, `evidence` — `aggregate_benchmark.py` depends on these names. Its `grading_provenance` object is mandatory and follows `references/schemas.md`.

   For an assertion that can be checked programmatically, write and run a script rather than eyeballing it — faster, more reliable, and reusable if the eval is ever run again.

2. **Aggregate into benchmark** — run the aggregation script from the skill-creator directory:
   ```bash
   python -m scripts.aggregate_benchmark <run-tmp>/eval_run/iteration-<N> --skill-name <name>
   ```
   This produces `benchmark.json` and `benchmark.md` with pass_rate, time, and tokens. With a single configuration there is nothing to subtract from, so the script omits `delta` entirely and `benchmark.md` has one column — a `+0.00` beside the pass rate would read as a measured comparison when nothing was compared. If generating benchmark.json manually, see `references/schemas.md` for the exact schema. `benchmark.md` is human-readable — it is what you show the user for the quantitative side.

3. **Do an analyst pass** — read the benchmark data and surface what the pass rate hides. Write your findings to `<run-tmp>/eval_run/iteration-<N>/analysis.md`; a pass you do only in your head is one you will skip, and this file is what makes the numbers readable later. Answer at least these three, explicitly:

   - **Which assertions would a bare model have passed anyway?** No measurement answers this, so answer it by reading: for each assertion that passed, ask whether a capable model with no skill would have satisfied it. Those are non-discriminating — they inflate the pass rate without evidence that the skill did anything. If *every* assertion in an eval is non-discriminating, that eval measured nothing: say so plainly, rather than letting a high pass rate stand as evidence the skill works.
   - **What does one run per eval let you say?** One run gives you no variance estimate, so nothing here supports a claim about reliability or consistency. Report the pass rate as a single observation, and name any eval whose result looks like it could have gone either way.
   - **Which FAILs should have been NOT_VERIFIABLE?** A missing renderer or an absent GUI is an environment gap, not a skill defect. Charging it to the skill both understates the skill and misdirects the fix: one calls for editing the skill, the other for fixing the environment.

4. **Review the results according to the selected mode.** In human-feedback mode, present both the qualitative outputs and the quantitative benchmark to the user. Walk them through it in the conversation:
   - For each test case, show what the skill produced (render or inline the output files where you can) alongside its prompt.
   - Show the `benchmark.md` summary — pass rate, timing, token usage — and your `analysis.md` observations.
   - If the harness offers a richer way to surface artifacts or collect a structured review, use it; otherwise present directly in the conversation.

   In `agent-self-iterate` mode, do not wait for or invent user feedback. Read the artifacts,
   `grading.json`, executor `user_notes.md`, `benchmark.json`, and `analysis.md` yourself. Treat only
   concrete evidence from those files as feedback.

5. **Complete the review.** In human-feedback mode, tell the user something like: "Here are the results
   for each test case, plus the benchmark numbers. Take a look and let me know what you think —
   anything that looks off, or that you'd want done differently." In agent-self-iterate mode, continue directly
   to the iteration decision below.

### Step 5: Decide whether to iterate

In human-feedback mode, the user reviews the outputs and tells you what they think. No feedback on a
test case means they were fine with it only after they had the opportunity to review it. Capture the
substance of what they say (which test case, what was wrong).

In `agent-self-iterate` mode, make the decision from the archived evidence. Classify each issue
before editing:

- a skill defect — the instructions were missing, wrong, misleading, or repeatedly forced the same
  workaround;
- an eval defect — the prompt or assertion was ambiguous, impossible, or non-discriminating;
- an environment gap — required evidence or execution capability was unavailable;
- run variance or execution failure — one observation did not establish a skill defect.

Only a skill defect justifies editing the skill. Fix eval defects in the next iteration's eval
definition, report environment gaps as NOT_VERIFIABLE, and do not rewrite the skill to compensate
for run variance.

---

## Improving the skill

This is the payoff. You have a measured iteration and either real user feedback or agent-reviewed evidence.
Turn that input into a concrete improvement proposal. Spend the thinking here, but keep the measured
skill unchanged unless another complete iteration can run.

### How to think about improvements

1. **Generalize from the feedback.** The big picture thing that's happening here is that we're trying to create skills that can be used a million times (maybe literally, maybe even more who knows) across many different prompts. Here you and the user are iterating on only a few examples, because it helps move faster. The user knows these examples in and out and it's quick for them to assess new outputs. But if the skill you and the user are codeveloping works only for those examples, it's useless. Rather than put in fiddly overfitty changes, or oppressively constrictive MUSTs, if there's some stubborn issue, you might try branching out and using different metaphors, or recommending different patterns of working.

2. **Keep the prompt lean, and be careful what lean means.** Read the transcripts and `user_notes.md`, not just the final outputs. Content that sent the model down an unproductive path, or that turned out untrue in this environment, should go. Content the model turned out not to need is a different case: the run shows what this model didn't need, and says nothing about whether a weaker one can do without it. Demote that into `references/` instead of deleting it — a strong model skips it, one that needs it can follow the pointer.

3. **Explain the why.** Try hard to explain the **why** behind everything you're asking the model to do. Today's LLMs are *smart*. They have good theory of mind and when given a good harness can go beyond rote instructions and really make things happen. Even if the feedback from the user is terse or frustrated, try to actually understand the task and why the user is writing what they wrote, and what they actually wrote, and then transmit this understanding into the instructions. If you find yourself writing ALWAYS or NEVER in all caps, or using super rigid structures, that's a yellow flag — if possible, reframe and explain the reasoning so that the model understands why the thing you're asking for is important. That's a more humane, powerful, and effective approach.

4. **Look for repeated work across test cases** — this is the detector for a step that should have been low-freedom. Read the transcripts and notice if the subagents all independently wrote similar helper scripts or took the same multi-step approach to something. If all 3 test cases resulted in the subagent writing a `create_docx.py` or a `build_chart.py`, that's a strong signal the skill should bundle that script. Write it once, put it in `scripts/`, and tell the skill to use it. This saves every future invocation from reinventing the wheel.

This task is pretty important (we are trying to create billions a year in economic value here!) and your thinking time is not the blocker; take your time and really mull things over. I'd suggest writing a draft revision and then looking at it anew and making improvements. Really do your best to get into the head of the user and understand what they want and need.

### Propose, verify when possible, then report honestly

Write `<run-tmp>/eval_run/iteration-<N>/proposed_changes.md`. For each proposal, name the observed evidence, the target section or bundled resource, the intended edit, and the evals that must be rerun. Do not modify the shipped skill when no verification iteration remains.

In human-feedback mode, apply a proposal only after the user asks for another round. In agent-self-iterate mode,
apply it when the evidence supports a skill defect and the iteration cap and remaining wall-clock
budget allow one more complete round. Snapshot the changed skill as iteration `N+1`, then rerun
**all active evals** against it — a prompt change can affect behavior outside the one failure that
motivated it. Only a completed iteration can promote that revision.

Stop when any of these holds:

- no evidence-backed skill change remains;
- the selected mode's iteration cap is reached;
- there is not enough budget to apply, execute, grade, aggregate, and analyze another full round.

If an attempted next iteration is incomplete or exposes a regression caused by the edit, restore
the last promoted snapshot. Keep the round and its evidence; mark an incomplete run's
`measurement_status` as `failed`, or a complete regression's `promotion_status` as `rejected`. If no
next iteration can run, leave the unapplied idea in `proposed_changes.md` and deliver the last
complete, promoted revision.

What you report must keep those revisions distinct:

- **Say what was measured and what wasn't.** Each benchmark describes the matching
  `<run-tmp>/eval_run/skill_snapshots/iteration-<N>/`. Proposed edits are reasoned from that evidence,
  not validated by it until the next iteration finishes. State which proposals address an observed
  failure and which are judgment.
- **A clean sweep is ambiguous, not good news.** A pass rate of 1.0 on the first and only pass more often means the assertions were easy for the bare model than that the skill is finished. Take the non-discriminating list from `analysis.md` seriously before reporting success.
- **Never soften an assertion to make the number better.** If you catch yourself rewording an assertion so it passes, stop and report the failure instead — that impulse means the number has stopped measuring the skill. Genuinely broken assertions (unsatisfiable as written, or checking something the inputs can't exercise) are a different thing: fix them, and say in your summary that you did and why.
- **Leave completed iterations alone.** Never back-edit an iteration's `eval_metadata.json`,
  `grading.json`, `benchmark.json`, or snapshot to match a later skill. An archive that contradicts
  its own grading stops being citeable.

Human-feedback mode returns to the user after each measured iteration. Agent-self-iterate mode continues without
pausing only while the evidence, cap, and remaining budget all permit another complete iteration.

---

## Environment adaptations

The workflow above assumes subagents. If they are not available:

- Run test cases serially. For each one, read the skill's SKILL.md, then follow its instructions to accomplish the test prompt yourself. This is less rigorous (you wrote the skill and you're also running it), but the user's review of the outputs compensates.
- Grade inline with `grading_provenance.mode = "inline_fallback"`, skip quantitative claims that imply independent grading, and focus on qualitative feedback from the user. Timing and token data from your own turn aren't comparable to a subagent run's.
