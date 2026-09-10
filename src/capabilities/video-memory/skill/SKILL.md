---
name: qwen-mm-plugins-video-memory
description: "Triggered for long videos (30+ minutes), whether a single file or a directory of multiple videos. Vision-language MCP tools designed for efficient reading and semantic analysis of long videos (30+ minutes), supporting memory construction and semantic search."
---

# Long-Video Memory

Check the `qwen-mm-plugins-video-memory` tools in your tool list for full schemas and parameters.


## ROUTING RULE — Read This First

When a user asks about a video that is **longer than 30 minutes**, you MUST use this skill instead of `read_video`. `read_video` cannot meaningfully cover a multi-hour video — it samples too few frames once, which is ineffective and always misses important content. You MUST build memory first, then you can fast explore the whole long video with the help of your pre-built memory.

**Workflow:**
1. Check if `<video_path>.memory/` directory exists
2. **Memory exists** → query it using the `qwen-mm-plugins-video-memory` tools
3. **Memory does not exist** → build it first (see "Building Graph Memory" section), then query
4. After locating a specific segment via memory tools, you MUST use `read_video` with a narrow `start_time`/`end_time` for frame-level visual inspection, because memory is always coarse and maybe inaccurate.

## Hierarchical Graph Memory

The memory is a 4-level tree. You start broad (Root/SuperEvent) and drill down to find specifics:

```
Root (1 per video)
  └─ SuperEvent (10-20 story arcs)
       └─ MacroEvent (3-8 per SuperEvent, each ~3-8 min)
            └─ Subgraph (entities, events, OCR text, relations)
```

### Level Details

| Level | Contains | IDs |
|-------|----------|-----|
| **Root** | Title, description, themes, key entities, emotional tone | — |
| **SuperEvent** | High-level narrative arc, time range, key entities | `super_01`, `super_02`, ... |
| **MacroEvent** | Specific event segment with time range, summary, key entities | `macro_0001`, `macro_0002`, ... |
| **Subgraph** | Entity nodes (PERSON/OBJECT/LOCATION), Event nodes (timestamped actions), OCR text (on-screen text), and Edges (SEMANTIC/CAUSAL/TEMPORAL/HIERARCHICAL/SPATIAL/IDENTITY relations) | `macro_0001:ent_001`, ... |


## Building Graph Memory

If `graph_memory.json` doesn't exist for a video, build it:

```bash
# Build memory for a single video (output: <video_path>.memory/)
bash script/build_memory/build_memory.sh /path/to/video.mp4

# Build with custom options
bash script/build_memory/build_memory.sh /path/to/video.mp4 \
    --output-dir /path/to/output --model qwen3.7-plus --api-key "$DASHSCOPE_API_KEY"
```


## Retrieval Workflow

Most questions follow a 2-step pattern:

### Step 1: Locate (find the right macro_id)

Choose ONE entry point based on the question type:

| Question Type | Entry Tool | Example |
|---|---|---|
| References a specific time | `search_by_time(start_sec, end_sec)` | "What happens at 45:30?" |
| Asks about a person/action/event | `search_nodes(query)` | "What did the chef cook?" |
| Asks about on-screen text/scores | `search_ocr_text(query)` | "What was the final score?" |
| References a known story segment | `get_macro_events(super_id)` | "In the first quarter..." |
| Asks about event causality | `search_nodes(query)` → `get_subgraph` | "What caused the explosion?" |

### Step 2: Drill Down (get full detail)

Call `get_subgraph(macro_id)` on the 1-2 most relevant macro_ids from Step 1. This returns ALL detail: entities, events, OCR text, and relations. Answer from this data.

### Decision Flow

```
Question arrives
  ├─ Does the SuperEvent list already narrow it? → get_macro_events(super_id) → get_subgraph
  ├─ Time reference? → search_by_time → get_subgraph
  ├─ On-screen text/score? → search_ocr_text → get_subgraph (if needed)
  ├─ Person/action/event? → search_nodes → get_subgraph
  └─ Causal/relational? → search_nodes → get_subgraph (relations are in the subgraph)
```

## Tools Reference

### Navigation (hierarchy drill-down)

**get_summary** — Video-level root summary.
- Params: `video_path` (optional)
- Returns: title, description, themes, key entities, emotional tone
- Use when: you need a quick overview before drilling down

**get_super_events** — List all SuperEvents (high-level narrative arcs).
- Params: `video_path` (optional)
- Returns: super_id, label, time_range, key_entities, description, num_macros
- Use when: you need the overall structure or to pick the relevant story arc

**get_macro_events** — List MacroEvents under a SuperEvent.
- Params: `super_id` (optional), `macro_ids` (optional list)
- Returns: macro_id, time_range, label, key_entity_names, description
- Use when: initial context already narrows the segment

**get_subgraph** — Full detail for one MacroEvent. **Most information-rich tool.**
- Params: `macro_id` (required)
- Returns: Entities, Events, OCR texts, Relations
- Use when: you have a macro_id and need specifics

### Search (direct access)

**search_nodes** — Semantic search over Entity and Event nodes (excludes OCR).
- Params: `query` (required), `top_k`, `node_types` (`["event"]`, `["entity"]`, or both)
- Query guide: write a DESCRIPTIVE STATEMENT, not a question
  - Good: "A player scores with an alley-oop dunk"
  - Bad: "Which player scored the alley-oop?"
  - For multi-choice: extract SHARED info across options, exclude differing details

**search_ocr_text** — Semantic search over on-screen text only.
- Params: `query` (required), `top_k`
- Use for: scores, stats, jersey numbers, broadcast graphics
- Do NOT use search_nodes for OCR content — they are separate indexes

**search_asr_text** — Semantic search over ASR transcript text (speech, dialogue, narration).
- Params: `query` (required), `top_k`
- Use for: spoken content, dialogue, narration, or other audio/verbal information

**search_by_time** — Find MacroEvents by time range.
- Params: `start_sec`/`end_sec` (seconds), or `start_time`/`end_time` (`DAY{N} HH:MM:SS`, for EgoLife)
- Returns summaries only — follow up with get_subgraph for detail

**enumerate_events** — Enumerate ALL matching event instances in time order — built for COUNTING / "how many times / list every occurrence" questions.
- Params: `query` (required; a descriptive statement of the target event), `min_cosine`, `max_results`, `node_types` (default `["event"]`; use `["entity"]` for objects/people)
- Unlike search_nodes (top-k best matches), returns every match above a similarity threshold, deduped and sorted by start time
- If undercounting, retry with a lower `min_cosine` and alternative phrasings

### Video Frame Extraction

**read_video** — Extract frames from the original video after narrowing the time range via memory tools. `video_path` is in the initial context.

## Anti-Patterns (common mistakes)

1. **Answering from SuperEvent descriptions** — these are summaries. Always get_subgraph for specific details.
2. **Repeating similar searches** — if search_nodes found macro_0022, call get_subgraph(macro_0022) instead of searching with a slightly different query.
3. **Ignoring tool types** — if search_nodes gives poor results, try search_ocr_text or search_by_time. Different indexes cover different content.
4. **Guessing counts** — for "how many" questions, get_subgraph and count from the explicit node list. Never estimate from words like "several" or "multiple".
5. **Skipping tool calls** — never answer without querying the memory graph first, even if the question seems simple.

## Storage

Graph memory is stored next to the video, in `<video_path>.memory/` (e.g.
`/data/clip.mp4` → `/data/clip.mp4.memory/`). The server auto-loads it from there
when you pass `video_path`, or set `GRAPH_MEMORY_PATH` to point at one explicitly.

Contents: `graph_memory.json`, `embeddings.npz`, `01_macros.json`, `subgraphs/`

## Efficiency Rules

- Maximum 10 tool call rounds — use them wisely
- Most questions need: 1 search + 1-2 get_subgraph calls
- Always drill down before giving up — the answer is in the subgraph, not the summary
