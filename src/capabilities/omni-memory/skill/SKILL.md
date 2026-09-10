---
name: qwen-mm-plugins-omni-memory
description: "Build and query persistent stateful audio-visual memory for long videos, including people, speaker-attributed dialogue, paralinguistics, non-speech sound, events, semantic facts, and selective source-clip replay."
---

# Audio-Visual Memory

Check the `qwen-mm-plugins-omni-memory` tools in your tool list for full schemas and parameters.

## First decide whether this video needs a memory at all

Building one costs an omni call per 30s window, so over a short video it spends N calls indexing what
fits in a single request. Decide by length, and by whether the user wants a memory.

**How you learn the length: `get_memory_status(video_path=...)`.** It reports `duration_min` for the source video even when no memory exists, and its `next_step` already applies the table below — so the call you had to make anyway also answers this. (It reads the file with ffprobe; if that is unavailable the field is absent and building is the safe default.)

| Length | What to do |
|---|---|
| **under ~10 min** | `watch_and_answer(video_path, question)` — no memory, one call, done |
| **~10–30 min** | build a memory, *unless* the user has said they do not want one and just wants a quick answer — then `watch_and_answer` |
| **over ~30 min** | **build a memory. No exceptions.** One request cannot hold that much video, so a watch will fail rather than answer badly |

Two things that override the table:

- **Several questions about the same video → build the memory**, even a short one. A watch is stateless:
  every question re-uploads and re-watches, while a memory is paid for once and then answers for free.
  One question is cheaper as a watch; a conversation is cheaper as a memory.
- **A memory already exists → use it.** `watch_and_answer` re-uploads the video and sees only what is
  in it; the memory already holds every utterance with its speaker, and answers in milliseconds.

If a watch cannot get through, its result says so explicitly: `fallback: "build_memory"` plus the exact
command. Run that and continue with the flow below. When the failure is throttling or a misconfigured
endpoint there is no `fallback` field — retry or fix the configuration instead, because a build would
hit the very same wall.

## General Workflow

Everything below is the memory path. Memory must exist before you can query it. **When a user asks about an audio-video, you are the one who retrieves memory and answers** — every tool here returns evidence, not an answer.

1. `get_memory_status(video_path=...)` — **always start here.** It reports one of:
   - `exists: false` → also carries `duration_min`, and a `next_step` that applies the routing table
     above: short enough to watch instead, or build it (see "Building memory")
   - `exists: true, complete: true` → query it
   - `exists: true, complete: false` → **truncated.** Its answers cannot be trusted. An interrupted
     build still finalizes the library, so it looks normal otherwise; continue it with `--mode resume`
2. Query it (see "Retrieval workflow")
3. Answer from what you retrieved. The record already carries the audio's content — every utterance
   with its speaker, how it was said, and the non-speech sound around it — so this is where most
   questions end.

`replay_and_answer` is not step 4. It is an exception off the side of this flow, taken only when you can
name a specific thing the record does not contain — and it re-watches only the few clips you name, so on
everything else it knows less about the video than the memory does.

## The Memory Layout

Four retrievable containers plus the clip files, filled by one pass over the video:

```
Entities     canonical people: person_id, resolved name, appearance, attributes
Semantic     induced facts as keyed triples, e.g. David/role → "…"      ← by EXACT key
Episodic     one record per 30s window: visual caption + every utterance
             with its speaker_id, paralinguistics, acoustic events        ← by hybrid search
Scene env    durable environment/layout items, recalled on demand
Clips        the 30s files themselves, so an answer can re-watch the source
```

Three properties drive how you retrieve:

- **`person_id` (`P001`, `P002`, …) is stable for the whole video.** Names are inferred separately by
  reading the accumulated transcript, so a person may be `P003` with `name: null` until someone
  addresses them by name — expected, not a failure. For those people, `get_memory_overview` carries
  `also_heard_as` (the names they were heard called, e.g. `P003 also_heard_as ["Dara"]`); that is how
  you map a name the user says onto an anonymous id.
- **Semantic keys are an exact lookup, not a search.** Keys are built from the resolved *name*
  (`David/prefers/transport`). `get_memory_overview` gives you the complete directory, so you pick
  from it rather than guessing — a key that does not exist simply returns nothing. Note which way round
  this goes: the container named **Semantic** is the one reached by key, while hybrid search is what
  reaches **Episodic**. To find a fact by its content instead of its key, use `search_facts(query=…)` —
  a separate tool, not something a plan can do.
- **Environment items are on demand, not resident.** Recalling them for every question measured
  net-negative, so `include_scene` is a deliberate pick for "where is X" questions.

## Building memory

`build_memory/` runs through Bash with the system Python, outside the MCP server's `uvx`
environment. It requires Python 3.10+, `pip`, `ffmpeg`/`ffprobe`, and `DASHSCOPE_API_KEY`; missing
Python packages (`numpy<3`, `openai`) are installed automatically. Run
`qwen-mm-plugins-omni-memory --check-system` before building.

```bash
# one video → memory next to it, at <video>.memory/
python3 script/build_memory/build_memory.py /path/to/video.mp4 --model qwen3.5-omni-plus

# many videos → independent per-video memories, in parallel
python3 script/build_memory/build_memory.py --video-dir /path/to/dir -j 4 --model qwen3.5-omni-plus

python3 script/build_memory/build_memory.py /path/to/video.mp4 --mode rebuild  # discard and start over
```

`--model` is the omni model all three build stages use — per-clip extraction, semantic induction and
name alignment. It defaults to `qwen3.5-omni-plus` (or `$QWEN_MM_API_OMNI_MODEL` if set), so it can
be omitted; name it explicitly when you want the record to say which model produced the memory. The
endpoint comes from `DASHSCOPE_BASE_URL`, or DashScope's default host when it is unset.

The script writes its own log next to the memory (`build_<timestamp>.log`, overridable with `--log`)
and prints the model, endpoint and log path on startup, so there is nothing to redirect.

Paths above are relative to this Skill directory. Everything the build needs is inside
`script/build_memory/` — it imports nothing from the MCP server's package, so it runs the same whether
that package is installed or not.

### Streaming several videos into one memory

Give the same `--namespace` to several videos and they become **one continuous memory**:

```bash
python3 script/build_memory/build_memory.py --video-dir /path/to/session --namespace my_stream
python3 script/build_memory/build_memory.py next_part.mp4 --namespace my_stream --mode append
```

Each segment starts from the previous segment's state, so a person keeps the **same `person_id`**
across videos, semantic facts keep **accumulating and merging**, and timestamps are stitched end to
end into one timeline. Use it when several files are really one recording session. Segments must be
given in chronological order, run strictly serially (`-j` is ignored and reported), and a failed
segment stops the stream — later ones would start from missing state.

By default it lives at `<first-video-directory>/<namespace>/`; query it with both `video_path` and
`namespace`. Set `MEM_LOCAL_DIR` when you want a fixed shared library and namespace-only queries.
Videos appended in separate commands must share a directory unless `MEM_LOCAL_DIR` is configured.

Requirements: `ffmpeg`/`ffprobe` on PATH and `DASHSCOPE_API_KEY`. Omni calls use
`DASHSCOPE_BASE_URL` and `QWEN_MM_API_OMNI_MODEL` when configured.

## Retrieval workflow

### Step 1: pick an entry point by question type

| Question type | Entry tool | Example |
|---|---|---|
| What one person said | `get_person_dialogue(person_id)` | "What did David say about the budget?" |
| Who said something | `search_dialogue(query)` | "Who offered to book a restaurant?" |
| Who these people are | `get_people` | "Who is Allen? / who is in this video?" |
| A stable fact or relationship | `search_facts(query \| key_prefix \| subject_id)` | "What is Lucy's role?" |
| A specific time | `get_timeline(start_sec, end_sec)` → `get_moment` | "What happens around 12:30?" |
| Full detail of known clips | `get_moment(idxs)` | — |
| **Open-ended / needs several kinds of evidence** | **`plan_and_search`** | "What did they agree on in the end?" |

Targeted questions go straight to their tool — do not route everything through `plan_and_search`.

### Step 2: orient, plan, search, answer

For the open-ended case, **you** are the planner. `plan_and_search` executes the plan you give it and
never answers. One search per question:

```
① orient — get_memory_overview
   → people (person_id, name, appearance, also_heard_as)
   → semantic_key_directory   ← the complete list of fact keys; you pick from it, never guess
   → scene_env_available      ← a marker only; ask for the items via include_scene below

② plan it yourself, then ONE search
   plan_and_search(question="What did they agree on in the end?",
                   people=["P001","P002"],                    # from step ①
                   fact_keys=["event:campaign/decision"],      # from the key directory
                   queries=["two people settle on a plan",     # descriptive statements
                            "someone agrees to a proposal"],
                   time_ranges=[[1500, 1800]],                 # only if time-bounded
                   include_scene=False)                        # on only for "where is X"

③ read the evidence and answer.
   ↑ for most questions the flow ends here

   ⤷ exception, only if you can name the specific unrecorded detail you need:
     replay_and_answer(idxs=suggested_replay_idxs, question=...)
```

Step ① is not optional and not a probe: `fact_keys` is an exact lookup, so a plan that names no keys
gets no facts at all. The directory is what makes the plan possible — get it first, then plan once.
It stays in your context for the rest of the conversation, so one call covers every later question
about the same video.

If a search comes back thin, re-plan and search again — but change the *axis* (name people, name keys,
add a time range) rather than rewording the same queries.

### Decision flow

```
Question arrives
  ├─ Short video (<10 min), or user wants no memory (and it is <30 min)?
  │                                          → watch_and_answer — ends here, no memory involved
  ├─ get_memory_status                       ← never skip; truncated libraries look fine
  ├─ Names one person, asks what they said?  → get_person_dialogue
  ├─ Asks who said something?                → search_dialogue
  ├─ Asks about a fact / role / relation?    → search_facts
  ├─ Names a time?                           → get_timeline → get_moment
  └─ Open-ended?                             → get_memory_overview → plan → plan_and_search → answer
                                                                                              ↑ ends here

replay_and_answer is not on this flow — see Usage Rules
```

## Tools Reference

### Status and orientation

**get_memory_status** — Does the memory exist, is it complete, and does this video need one?
- Params: `video_path` or `namespace`
- Returns: `exists`, `complete`, `clips`/`episodic`/`planned_clips`, `people`, `named_people`,
  `semantic_facts`, `duration_sec`; `truncated` and `next_step` when incomplete
- When `exists: false` it also reports the SOURCE video's `duration_sec` / `duration_min` and a
  `next_step` that applies the routing table at the top — this is where you learn whether the video is
  short enough to answer with `watch_and_answer` instead of building anything
- Use when: always, first. `complete: false` means the answers are not trustworthy.

**get_memory_overview** — The vocabulary you plan with. **Read this before `plan_and_search`.**
- Params: `video_path` or `namespace`
- Returns: `people` (`person_id`, `name`, `appearance`, and `also_heard_as` for anyone still unnamed),
  `semantic_key_directory` (complete), `scene_env_available`
- Use when: before planning any open-ended retrieval. It costs one call and stays in your context for
  every later question about the same video.
- `scene_env_available` is a marker, not the items: pass `include_scene=True` to `plan_and_search`
  when the question needs them

### Answering an open-ended question

**plan_and_search** — ONE retrieval from a plan you decide, fusing the containers. **Does not answer.**
- Params: `question`, `people`, `fact_keys`, `queries`, `time_ranges`, `include_scene` (off by
  default — turn it on for "where is X"), `top_k` (default 5)
- Returns: `people`, `facts`, `moments` (briefs), `scene_env`, `evidence_text`,
  `suggested_replay_idxs`, `plan_used`
- `fact_keys` is an EXACT lookup — pick from `get_memory_overview`'s `semantic_key_directory`. Naming
  no keys returns no facts; that is why the overview comes first
- Query guide: `queries` take DESCRIPTIVE STATEMENTS, not questions
  - Good: "someone offers to bring an umbrella"
  - Bad: "who brought an umbrella?"
  - Several short angles beat one long query — each contributes its own `top_k` recall
  - These also decide `suggested_replay_idxs`, so describe what to look for

**replay_and_answer** — Re-watch clips **with their audio** and have the omni model report what it sees.
- Params: `question`, `idxs`, `evidence` (optional text context), `model` (optional omni model;
  defaults to `qwen3.5-omni-plus`, and need not match the model the memory was built with)
- Returns: `answer`, `watched_idxs`, `model`, `sent_mb`; `dropped_idxs` / `missing_idxs` when applicable
- It answers instead of returning evidence, because what it reads is in the video itself
- Watches at most 3 clips per call (~3.5 MB each, inline) — every other clip stays invisible to it.
  Extras come back in `dropped_idxs`
- Use when: one **specific** detail the 30s record demonstrably lost — a fleeting object, a facial
  expression, a count
- **Not for what was said.** Every utterance is stored verbatim with its speaker and paralinguistics,
  so `get_person_dialogue` / `search_dialogue` / `get_moment` answer wording questions better, and for
  free

### Answering without a memory

**watch_and_answer** — Watch a SHORT video whole, in one call, with **no memory involved**.
- Params: `video_path` (required — a source video, NOT a namespace), `question`, `model` (optional)
- Returns: `answer`, `sent_mb`, `duration_sec`, `transcode_cached`, `model`
- On failure: `error` + `failure` (`reject` / `timeout` / `empty` / `rate` / `config`). For the first
  three it also carries `fallback: "build_memory"` and `next_step` — run that command, then use the
  memory path. For `rate` (throttling) and `config` (wrong endpoint or key) there is deliberately **no**
  `fallback`: a build runs against the same endpoint and would fail identically
- Use when: the video is under ~10 min, or the user has said they do not want a memory built. See the
  routing table at the top — over ~30 min this is not an option
- The video is re-encoded once and that copy kept, so follow-ups skip the re-encode
  (`transcode_cached: true`). The omni call is still paid every time, which is why several questions
  are cheaper as a memory
- It answers rather than returning evidence for the same reason `replay_and_answer` does — what it
  reads is in the audio, which you cannot hear — but it sits **off** the retrieval flow, before there
  is anything to retrieve

### Direct access

**get_people** — Full dossier for everyone, or one `person_id`.
- Params: `person_id` (optional)
- Returns: `person_id`, `name`, `appearance`, attributes, first/last appearance, clip indices
- Use when: "who are these people", or you need one person's full record

**get_person_dialogue** — **Every** line one person spoke, in order.
- Params: `person_id` (optional — omit it for every speaker in time order), `start_sec`, `end_sec`, `limit`
- Returns: `total` and the utterances with timestamps and paralinguistics
- Use when: "what did X say" — this is exhaustive, where `plan_and_search` is top-k

**get_timeline** — Moments in a time range, chronologically.
- Params: `start_sec`, `end_sec` (`end_sec=0` means "to the end")
- Returns: brief per moment — follow up with `get_moment` for detail
- Use when: the question names a time and there is nothing to search for

**get_moment** — Full detail for specific clips. **Most information-dense tool.**
- Params: `idxs` (required)
- Returns: full `visual_caption`, `scene_continuity`, `scene_env_update`, per-person actions, every
  `utterance`, `acoustic_events`, and **`clip_path`**
- Use when: a brief is not enough. `clip_path` is the 30s file on disk, should you have another tool
  that can open it

**search_dialogue** — Semantic search at the UTTERANCE level; every line comes back with its speaker.
- Params: `query` (required), `top_k`
- Use when: "who said …" — you know the content but not the person

**search_facts** — Three ways into the semantic container.
- Params: one of `query` (search statements by content), `key_prefix` (`"David/"`, or a
  `person_id` prefix, which is mapped for you), `subject_id`; plus `top_k`
- Use when: you want facts by CONTENT rather than by exact key — `plan_and_search` cannot do this

**search_memory** — Broad hybrid search across containers, with entity-anchored multi-hop.
- Params: `query` (required), `top_k`
- Use when: you know nothing about the library and want one wide net. `plan_and_search` is usually
  the better entry point.

## Usage Rules

1. **On the memory path, never answer without checking status first** — a truncated library answers
   confidently from half a video, and looks entirely normal while doing it. (`watch_and_answer` is not
   on that path and needs no status check; it reads the video, not a library.)
2. **The text memory is the answer path; replay is the exception.** Before reaching for it, you must be
   able to name the specific thing you need that the record does not have. "The record is only a
   summary" is not such a reason — a replay sees only the clips you name and nothing else, so a vague
   replay returns a vaguer answer than the memory you already hold.
3. **Stop when the open-ended path and a replay have both failed.** `get_memory_overview` →
   `plan_and_search` → `replay_and_answer` is the deepest this memory goes; there is no further tool
   that knows more. Do not go fishing through the others — say what the evidence did establish and
   what it could not, and leave it there. More calls at that point produce a guess, not an answer.
