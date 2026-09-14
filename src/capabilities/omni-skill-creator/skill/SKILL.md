---
name: qwen-mm-plugins-omni-skill-creator
description: Create a new Agent Skill from a teaching video, screen recording, or demonstration. Reads the recording as one audio-visual timeline — what is shown and what is said, time-aligned — then writes a SKILL.md plus the frames, clips, and audio a consumer needs. Make sure to use this skill whenever someone hands over a video, tutorial, or screen recording and wants a reusable skill out of it, including when they say "learn from this video", "turn this tutorial into a skill", or "extract a skill from this recording" — and also when they describe wanting an agent to reproduce a workflow they have on video, even if they never use the word "skill".
---

# Omni Skill Creator

A skill for creating new skills from teaching videos and validating what it creates.

At a high level, the process of creating a skill goes like this:

- **Perceive the video** — read `references/perceiver.md` and perceive the teaching video, building a timestamped event log (`video_events.md`) as you go.
- Decide what you want the skill to do and roughly how it should do it
- Write a draft of the skill, along with a media plan for multimodal assets
- Run `validate_skill` (L1 structural check) before proceeding to evals
- Create a few test prompts and test the skill with them
- Review the results both qualitatively and quantitatively according to the selected feedback mode
  - While the runs happen in the background, draft quantitative assertions if there aren't any; in human-feedback mode explain them to the user, and in agent-self-iterate mode record their rationale in the iteration analysis
  - In human-feedback mode, present each test case's outputs and benchmark metrics to the user; in agent-self-iterate mode, inspect the same artifacts yourself without inventing human feedback
- Turn evidence-backed improvements into a proposal; apply them only when the affected test cases can be rerun
- Repeat the measured pass when the selected feedback mode calls for another revision
- Expand the test set and try again at larger scale when the workflow warrants it

Your job when using this skill is to turn the supplied teaching video or demonstration into a new skill. Help the user narrow down what the skill should do, perceive the recording to ground it, write a draft, write the test cases, run them, and present the evidence. Begin by reading `references/perceiver.md` and perceiving the recording into `video_events.md`; if that timeline was already produced earlier in the same conversion, continue from it rather than repeating the read.

Of course, you should always be flexible and if the user is like "I don't need to run a bunch of evaluations, just vibe with me", you can do that instead.

Then after the skill is done (but again, the order is flexible), you can also run the skill description improver, which we have a whole separate script for, to optimize the triggering of the skill.

Cool? Cool.

## Communicating with the user

Users of this skill range from non-developers who have just opened a terminal to seasoned engineers, so read the context cues and pitch your wording accordingly. For calibration: "evaluation" and "benchmark" are borderline but usually fine; "JSON" and "assertion" want real evidence the user knows them before you use them unexplained. When in doubt, define the term in a clause and move on.

---

## Creating a skill

### Capture Intent

Start by understanding what the user wants the skill to do. For this skill the primary source is usually a teaching video or demonstration — the `video_events.md` timeline (and its `highlights` lines) is where you read off the demonstrated workflow: the steps and their order, the tools/UI used, and the corrections or gotchas the presenter surfaced. A prior conversation can be a secondary source (e.g., they say "turn this into a skill" about something you just did together). Either way, the user may need to fill the gaps, and should confirm the intent before you proceed.

1. What should this skill enable you to do?
2. When should this skill trigger? (what user phrases/contexts)
3. What's the expected output format?
4. Should we set up test cases to verify the skill works? Skills with objectively verifiable outputs (file transforms, data extraction, code generation, fixed workflow steps) benefit from test cases. Skills with subjective outputs (writing style, art) often don't need them. Suggest the appropriate default based on the skill type, but let the user decide.

### Interview and Research

Proactively ask questions about edge cases, input/output formats, example files, success criteria, and dependencies. Wait to write test prompts until you've got this part ironed out.

Check available MCPs - if useful for research (searching docs, finding similar skills, looking up best practices), research in parallel via subagents if available, otherwise inline. Come prepared with context to reduce burden on the user.

Bound exploratory debugging. After two materially different failed attempts to resolve the same non-blocking fact, record the uncertainty and the best reversible verification step, then continue drafting. Do not spend the authoring budget perfecting behavior in an unavailable application; reaching a complete, source-grounded first draft is more valuable than exhausting the context before any skill exists.

### Write the SKILL.md

Based on the user interview (and `video_events.md` if a video was provided), fill in these components:

- **name**: Skill identifier
- **description**: When to trigger, what it does. This is the primary triggering mechanism - include both what the skill does AND specific contexts for when to use it. All "when to use" info goes here, not in the body. Note: models tend to "undertrigger" skills -- to not use them when they'd be useful. To combat this, please make the skill descriptions a little bit "pushy". So for instance, instead of "How to build a simple fast dashboard to display internal data.", you might write "How to build a simple fast dashboard to display internal data. Make sure to use this skill whenever the user mentions dashboards, data visualization, internal metrics, or wants to display any kind of company data, even if they don't explicitly ask for a 'dashboard.'"
- **compatibility**: Required tools, dependencies (optional, rarely needed)
- **provenance fields** (required for video-derived skills): `source_type`, `source_path`, `source_sha256`, `extraction_date`, `status` (one of `source-grounded` or `execution-verified`)
- **how to tell it worked**: what the consumer should see after a step, and how to confirm the end state. This matters most when L3 didn't run — then the first real use *is* the verification, and whoever is doing it needs to know what to look for. Name a reversible trial where one exists (work on a copy, fill the form without submitting, stage without committing) so the first attempt isn't also the commitment.
- **the rest of the skill :)**

> **Preserve the highlights.** Each event in `video_events.md` carries a `highlights` line — the presenter's preferences, the *why* behind choices, points emphasized, and caveats/gotchas (from speech, the visuals, or both). This is the tacit expert judgment that separates a genuinely useful skill from a mechanical list of steps. Carry it into the skill as preferences, rationale ("why"), and warnings — don't flatten it away when you turn events into steps.

> **Anchor on the agent's action space — and don't assume it is yours.** A demo shows a *human's* action space; retarget each step to how an agent would go about it rather than transcribing the motions. That space is hybrid: code tends to suit heavy, exact, repetitive work, while the GUI suits whatever has to be seen and clicked and gives real feedback from the running system.
>
> **Preserve both routes and let the consumer pick** — which half it has isn't visible from here and won't stay fixed. Record each route with what it needs ("with the application open: …" beside "headless, via a script: …"), including one you couldn't run yourself. Where you found only one, write it as a requirement on the step — "needs the application open; no scripted route known" — rather than as an impossibility, which is a claim about every future toolchain and will stop a more capable consumer from even looking.
>
> Keep the two registers apart. Everything above is addressed to you, the author; the skill you write is addressed to a consumer who wasn't here and doesn't need your process. So state what a step *requires*, not what you went through to find it — what you tried, what you couldn't confirm, and why an asset was chosen belong in `.build/` and the provenance fields, not in the skill body.
>
> How tightly each step is specified is a separate axis from which route it takes — see *Degrees of Freedom* below.

### Multimodal Asset Management

When creating a skill from a video, you also need to manage multimodal assets. Here's how.

**Match the carrier to the kind of thing you are carrying.** This is a classification, not a default with exceptions. Two kinds:

- **Linguistic content** — names, paths, values, settings, the order of steps, code. Text is not merely adequate here, it is *better*: exact, searchable, copyable, diffable. An image of a string is a downgrade of that string.
- **Perceptual content** — what something looks like, how parts sit relative to each other, how a thing changes, how it sounds, what "right" looks like when you see it. Here the medium *is* the information. Prose is a summary of it, and a consumer who has to recognise, match or reproduce the thing cannot recover it from the summary, however well written.

So ask which kind you have — not whether you *could* describe it. "I can write a sentence about this" is true of nearly everything and settles nothing. And judge it for the skill's consumers, not for the environment you happen to be sitting in: you cannot see whether the skill will be loaded by something that can run this workflow — for which an asset is how it checks its own result against the demonstration — or by something that cannot and has to guide a person instead. Both need the perceptual content, and the skill outlives whatever is installed here.

1. **Plan a media_plan**: List the assets (frames, clips, audio segments) you intend to extract, each with a timestamp and a necessity rationale. Write it to `.build/media_plan.md` before you extract anything — the plan is what comes *before* capture, and on disk it leaves a record that the assets were chosen deliberately rather than collected and justified afterwards. (`asset_manifest.json` comes later, at step 3: it registers files that exist, so it can only confirm this plan, never stand in for it.) You can keep iterating on the plan as you write the skill.
   - **Necessity rationale**, for every asset: name the perceptual thing a consumer would have to recognise, match or reproduce. If you can't name it, you're extracting out of habit.
   - Deduplicate by timestamp at the planning stage.
   - Decide per frame **whether** it gets annotated and why — see step 2. Sometimes the answer is no.
   - **Keep clips and audio to the shortest span that carries the point** — no lead-in, no trailing filler. The advisory over-length WARNING (clip > 15 s, audio > 30 s) is a ceiling, not a target; a clip near it usually means you replayed a passage instead of pointing at a moment. When the motion itself is not the point, a frame plus a sentence beats a clip.
   - **When the content *is* a change over time, or a sound, no still can carry it.** Two frames show two states, not the transition between them, and the transition is sometimes the whole lesson. So this constrains length, not existence: "point, don't replay" means three seconds instead of thirty, not zero instead of three. Assets do cost storage and context, and the set should stay small — but the cost is the visible half and the loss is the invisible one, so shipping nothing from a recording worth recording is the easier mistake to make.

2. **Extract assets**: Use the available MCP tools to extract frames, clips, and audio segments according to your media_plan. Create `assets/` subdirectories on demand — don't pre-create empty `frames/`/`clips/`/`audio/`; empty dirs just clutter the shipped skill.

   **Annotate when the frame points at something; ship it clean when the frame *is* the something.** An annotation answers one question — *which part of this matters?* — so a frame whose job is to single out a control, a region or an order of steps normally wants marks, and shipping it raw leaves the consumer hunting. That is the common case, so treat marks as the default there.

   The exception is real, though, and worth taking: marks cover pixels and freeze one reading of the frame. When the frame's value *is* the whole picture — a layout, a finished result, a state to be recognised on sight — marks subtract from it and the clean frame is the better asset. Ship clean deliberately then, and say in the rationale that you did; it should read as a decision, not as the step you skipped. And prefer cropping to covering wherever it works: a tight crop directs attention without hiding anything, and often needs no marks at all. When you do mark, put text and numbers in empty space beside the subject rather than over the content — the tool draws exactly where you point it and will not move things to avoid occlusion.

   **When the subject isn't a rectangle, cut it out instead of cropping it.** A rectangle drags the surrounding background along, and an asset carrying someone else's background can't be placed on the consumer's own page. `cutout_frame` returns a transparent PNG plus the measured outline: pass a `region` (a `grounding` box, padded a little) and `background_seeds` — points on the background around the subject, one per distinct shade — and it finds the real pixel edge by flooding that background. Two cases make it the right tool without further thought: the asset's value is **the subject itself** rather than the screen it sat on, or **the skill teaches isolating a subject from its background**, where the cut-out *is* the demonstrated result. Read `coverage`, `border_inside_fraction` and `warnings` before registering it — they are what catch a seed that landed on the subject. It needs a fairly uniform background; over a photo or gradient, pass an explicit `polygon`. `crop_frame` stays the cheaper choice for genuinely rectangular regions.

   **Measured coordinates or none.** Arrows and callouts only have to point roughly right, so eyeballing them is fine. A box or circle has to *sit on* its target, and an estimate misses — so measure, then pass pixels with `coord_space="pixel"`. Three sources, cheapest first:
   - **you already cropped that area** — the crop's `region` *is* the pixel box, and its result carries `frame_size`, so the same spot on the full frame is `[x, y, x+w, y+h]`. Free, and if you have been studying a region you already have it;
   - **the target carries text** — `ocr_frames` with `locate` returns its pixel box. Offline and exact;
   - **the target has no text** — `grounding`, described in words, returns a pixel box.

   If you find yourself cropping one timestamp several times, each time nudging the region because the last framing was off, stop and measure: the fifth guess lands no better than the first. Estimate only when none of the three apply, and then say so in the rationale rather than presenting a guess as precise. (The `norm` 0-1000 space scales each axis separately, so on a wide frame one x unit and one y unit are different distances and eyeballed x drifts badly.) Finally, check the close-up the tool returns — if a mark missed, get measured coordinates and redraw. The annotated PNG is what you register.

3. **Build `asset_manifest.json`**: Every asset must have an entry with at minimum: `id`, `type`, `path`, `when_to_use`, `text_description`. See `references/schemas.md` for the full schema. Reconcile it against `.build/media_plan.md`: a planned asset that never materialized and an extracted asset that was never planned both need resolving — capture it, drop it, or note in the plan why it changed. Don't let the manifest quietly become a record of whatever happened to get captured.

4. **Run `validate_skill`** (L1 check) to catch structural issues before proceeding to evals.

### Skill Writing Guide

#### Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description, provenance fields)
│   └── Markdown instructions
├── asset_manifest.json          - Asset registry (required if assets/ exists)
├── .build/                      - Build artifacts (not distributed with skill)
│   ├── video_events.md          - Perception event log (the skill's provenance record)
│   └── media_plan.md            - Which assets were chosen, and why (written before capture)
└── Bundled Resources (optional)
    ├── scripts/    - Executable code for deterministic/repetitive tasks
    ├── references/ - Docs loaded into context as needed
    └── assets/     - Multimodal assets
        ├── frames/ - Keyframes (MMmSSs[_label].png)
        ├── clips/  - Video segments (MMmSSs_MMmSSs.mp4)
        └── audio/  - Audio segments (MMmSSs_MMmSSs.mp3)
```

#### Progressive Disclosure

Skills load in three levels, so context is only spent when it's needed:
1. **Metadata** (name + description) — always in context, ~100 words
2. **SKILL.md body** — loaded whenever the skill triggers; keep it under 500 lines
3. **Bundled resources** — read or executed on demand, no size limit (a script's source never enters context)

Approaching the 500-line limit is the signal to add a layer of hierarchy rather than to keep writing: move the detail into `references/` and leave a clear pointer saying when to go read it. Give reference files over ~300 lines a table of contents. When a skill covers several variants (frameworks, platforms, domains), split them one-per-reference so only the relevant one gets read.

#### Principle of Lack of Surprise

This goes without saying, but skills must not contain malware, exploit code, or any content that could compromise system security. A skill's contents should not surprise the user in their intent if described. Don't go along with requests to create misleading skills or skills designed to facilitate unauthorized access, data exfiltration, or other malicious activities. Things like a "roleplay as an XYZ" are OK though.

#### Degrees of Freedom

Match how tightly a step is specified to the task's **fragility** and how much legitimate **variation** it has. Naming the tier is useful — it is how you say later that a step was pitched at the wrong one.

- **High — prose.** Several approaches work and judgement picks between them. A script here only gets in the way.
- **Medium — a recipe with values.** A preferred way exists but variants are fine. Give the ordered steps and the concrete settings; not pseudocode, which reads as precision the step doesn't have.
- **Low — a bundled script.** The operation is brittle, order-dependent, or would be rebuilt from scratch on every run. Write it once into `scripts/` and point at it.

Deviation is cheap in an open field and expensive on a narrow bridge, so spend determinism accordingly — and don't buy it where it isn't needed.

#### Writing Patterns

Prefer using the imperative form in instructions.

When the skill must pin an output shape, show the template literally (a `## Report structure` block listing the exact headings) rather than describing it. Worked examples help too — an input paired with the output it should produce; adapt the labels if "Input"/"Output" reads oddly for the domain.

### Writing Style

Try to explain to the model why things are important in lieu of heavy-handed musty MUSTs. Use theory of mind and try to make the skill general and not super-narrow to specific examples. Start by writing a draft and then look at it with fresh eyes and improve it.

### Test Cases

After writing the skill draft, come up with realistic test prompts — the kind of thing a real user would actually say. **Coverage sets the count:** every distinct thing the skill claims to do needs at least one prompt that exercises it, so a narrow skill needs few and a broad one needs more. One prompt can cover several capabilities where a real user would ask for them together, which is both more realistic and cheaper than splitting them. For expensive or stateful work, default to at most three active evals; exceed that only when a distinct claimed capability would otherwise go untested, and record the coverage reason. Each prompt costs one executor run plus one grader run per iteration. Share them with the user: [you don't have to use this exact language] "Here are a few test cases I'd like to try. Do these look right, or do you want to add more?" Then run them.

Save test cases to `evals/evals.json`. Don't write assertions yet — just the prompts. You'll draft assertions in the next step while the runs are in progress.

**Make each eval task self-contained.** The executor must receive the task, input files, initial state, and target requirements needed to understand the requested result without seeing the teaching video. Replace references such as "do what the tutorial shows" or "use the settings above" with concrete requirements, and supply any task-specific reference image or input asset. Keep the demonstrated method in the skill; do not copy its solution into the task just to repair missing context. Before spawning the run, review the prompt and inputs without the video and resolve missing requirements. Grader-only source evidence may check fidelity to the demonstrated method, but it must not introduce an undisclosed target the executor could not know.

**At least one prompt must ask for the artifact itself**, whenever any route can produce a file worth checking. A set made entirely of "how do I…", "what's the fastest way…", "explain the difference…" prompts grades what the answer *says*; it never finds out whether following the skill produces the thing. Ask for the deliverable in a way a real user would — "save the finished `.pptx`", "give me the `.docx`" — and let the rest of the set cover the advice. Where the capability genuinely has no route to a checkable file, because it only exists inside a GUI, that is a limit to declare (`NOT_VERIFIABLE`, and `run_l3` if nothing can run at all), not a reason to reshape every assertion into "recommends X".

**When a task needs input material, prefer the real thing** — crop a frame from the source video, or recreate the material it demonstrates, and use that as the eval's `files`. Synthesize input only when the video can't supply it.

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
      "prompt": "User's task prompt",
      "initial_state": "not applicable",
      "isolation": "isolated",
      "expected_output": "Description of expected result",
      "files": []
    }
  ]
}
```

See `references/schemas.md` for the full schema (including the `assertions` field, which you'll add later).

### Pre-eval Verification (L1 + L2)

Before running evals, run through the three-layer verification framework (see `references/eval-layers.md`):

**L1 — Structural Validation:**
Run `validate_skill` on the skill directory. Fix all ERRORs and re-run until it passes. WARNINGs are worth reviewing but don't block.

**L2 — Asset Pre-checks** (automatic when assets exist):
- Rule-based checks (#25-#28): `validate_skill` also runs necessity_rationale completeness, index-manifest consistency, orphan asset detection, and the advisory over-length nudge. These produce WARNINGs.
- Dedup checks (#23-#24): If the skill has image or audio assets, call `dedup_frames` (for frames/clips) and `dedup_audio` (for audio segments) MCP tools. Report duplicates as WARNINGs.
- No `asset_manifest.json` → L2 checks are automatically skipped.

**L3 gate check:**
Read `evals/evals.json` for `eval_config.run_l3`. If `false`, report L1+L2 results and stop. If `true` or absent (default), choose the review mode once before L3 and record it in `eval_config.review_mode`:

- `human-feedback` is the default. Present each measured iteration to the user and wait for actual feedback before changing the skill.
- `agent-self-iterate` applies only when the caller explicitly says no human feedback is available **and** authorizes autonomous iteration. Do not wait for or invent human feedback; use artifacts, grader results, `user_notes.md`, `benchmark.json`, and `analysis.md` as the feedback signal.

Then read `references/eval-and-iterate.md` and follow it. L3 remains single-arm: one `with_skill` execution plus one grader per active eval in each iteration. It answers "can an agent do this task with this skill" — nothing compares the result against what a bare model would produce, so it is not a measure of how much the skill adds. Human-feedback use runs one iteration unless the user asks for another. Agent-self-iterate use defaults to at most two iterations unless the caller sets a different positive cap. A changed revision becomes deliverable only after all active evals rerun against it; otherwise keep the last measured revision and leave the change in `proposed_changes.md`.

Set `run_l3` to `false` yourself when running the task would have real consequences — it spends money, sends something, books something, changes a live system, or needs an account or permission that isn't yours to use. L3 answers its question by *doing the thing*; where the thing can't be undone, each eval run is a real action. That is a reason to decline the measurement, not to invent an assertion that a written plan can satisfy. Ship it `source-grounded`, say which parts went unverified, and give the skill what a first real run needs.

## Description Optimization

The `description` field in SKILL.md frontmatter is the primary mechanism that decides whether you invoke a skill, so after the skill works, offer to optimize it for triggering accuracy. It's a self-contained sub-procedure — generate trigger eval queries, run the optimization loop (`scripts/run_loop.py`), then show the user before/after and apply the best description. You write the eval labels the loop optimizes against and nobody reviews them first, so `references/description-optimization.md` is strict about which queries earn a place in the set. Read it and follow it.

---

### Package and Present (only if `present_files` tool is available)

Check whether you have access to the `present_files` tool. If you don't, skip this step. If you do, package the skill and present the .skill file to the user:

```bash
python -m scripts.package_skill <path/to/skill-folder>
```

After packaging, direct the user to the resulting `.skill` file path so they can install it.

---

## Environment Adaptations

The main workflow assumes subagents. The eval-specific adaptations (no subagents) are in `references/eval-and-iterate.md` under "Environment adaptations". What remains here applies regardless of L3:

**If API access for the triggering model is not available** (e.g., no `DASHSCOPE_API_KEY` or reachable OpenAI-compatible endpoint):
- Skip description optimization (`run_loop.py` / `run_eval.py`). These scripts judge triggering by calling the model through an OpenAI-compatible API, so they can't run without it.

## Output Structure

Everything in "Anatomy of a Skill" above ships with the skill, with one exception: `.build/` is a
work record, not a deliverable. It holds `video_events.md` (the perception log) and
`media_plan.md` (the capture plan) — keep both, since together they are how anyone later can tell
what the skill was drawn from and why its assets were chosen, but don't distribute them.

---

## Reference files

The agents/ directory contains instructions for specialized subagents. Read the relevant one when you spawn that subagent.

- `agents/grader.md` — L3: evaluate assertions against execution outputs. The base grader derives an evidence ladder for whatever artifact it is handed — it is format-agnostic, so no per-format scene overlay is needed

L3 trait overlays (zero or more apply, and they stack on top of the base grader):
- `agents/grader-trait-animation.md` — anything meant to move; why a flattening renderer is not a disproof
- `agents/grader-trait-audio.md` — anything meant to be heard; the ffprobe/astats/project-file ladder

The references/ directory has documentation you read on demand:
- `references/perceiver.md` — How to perceive a teaching video and build `video_events.md` (you follow this yourself when perceiving)
- `references/description-optimization.md` — The description-optimization loop (optional, after the skill works)
- `references/schemas.md` — JSON structures for evals.json, grading.json, etc.
- `references/eval-layers.md` — Three-layer verification framework reference (L1/L2/L3)
- `references/eval-and-iterate.md` — The full single-arm L3 path: measure one iteration, review it through human-feedback or agent-self-iterate mode, and promote only revisions that complete a following iteration (read when `run_l3` is true)

---

Please add steps to your TodoList, if you have such a thing, to make sure you don't forget. Specifically put "Create evals JSON and present test-case outputs to the user for review" in your TodoList to make sure it happens.

Good luck!
