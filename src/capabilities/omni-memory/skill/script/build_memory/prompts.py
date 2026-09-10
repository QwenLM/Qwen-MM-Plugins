# Trailing whitespace inside the prompts is intentional — do not strip it.
"""The prompts a build makes its omni calls with. stages.py fills the {{PLACEHOLDER}} markers.

Every one is a RAW string: the JSON examples inside contain backslash-escaped quotes that a normal
literal would silently unescape.

They are Python rather than .md data files because a wheel install only ships what package-data
covers, and a prompt file that goes missing turns into an empty model call rather than an error.
Keeping them as code makes that impossible.

The read path has its own prompts.py, with the two prompts it still calls a model for. The two files
share no prompt — this is a split, not a copy.
"""

# Extraction: one omni call per clip over video+audio → a composite {visual, audio} record.
# Part B6 is what binds each spoken line to a person_id.
SW_PROMPT = r"""You are a rigorous, objective **video + audio analyst** operating inside a **sliding-window memory pipeline** with an **accumulative scene-environment memory** and **canonical entity (incl. name) grounding**. You are given one ~30-second video clip (frames **and** an audio track) that is part of a longer video processed as overlapping windows. In a **single pass** produce BOTH parts below, output as **one** JSON object with exactly two top-level keys `visual` and `audio`:

- **(A) VISUAL** — detached third-person record of people/actions/objects/space, plus sliding-window continuity / de-duplication / entity-alignment / accumulative-environment / **name-grounding** signals.
- **(B) AUDIO** — verbatim transcription **with timestamps**, then paralinguistics and environmental sounds.

Division of labour: spoken *words* belong ONLY to audio (verbatim); visual records *visible communicative behavior* but never the words.

================================================================
============== SLIDING-WINDOW INPUT (read this first) ===========
================================================================
This clip is one window of a longer video; windows overlap. Time layout in **absolute original-video seconds**:
- **window_range** (full clip span): {{WINDOW_RANGE}}
- **context_range** (leading overlap already covered by PREVIOUS clip — continuity/alignment ONLY): {{CONTEXT_RANGE}}
- **target_range** (the genuinely NEW portion — PRIMARY source of new memory): {{TARGET_RANGE}}

Clip internal time 0 = start of window_range. **Convert every timestamp to absolute original-video seconds by adding the window-start offset.**
Rules: ① context_range = continuity only, do NOT re-commit. ② target_range = where new memory comes from. ③ if an utterance/action straddles the boundary, record in full and set `boundary_overlap=true`. ④ if context_range is `none`, this is the FIRST clip — whole window is new.

================================================================
========== CONTINUITY PRIOR — previous_state (optional) =========
================================================================
Carried over from previous clip(s). MAY contain: `scene_id`/`scene_summary`, `scene_env_known` (env elements already stored), `known_entities` (each `person_id`, **`name`**, `appearance`, `last_location`, `last_action`), `active_event_id`/`active_event_summary`, `last_processed_until`, and `global_context` (a rolling high-level summary of the whole video so far — use it ONLY as background to resolve references/continuity; do NOT re-commit it as new memory).

【previous_state】
{{PREVIOUS_STATE}}

Use it ONLY as a prior to align scene/entity/name/event IDs and to know what is already stored. **Current frames+audio are the only source of facts**; do NOT copy prior facts into your caption/transcript. If a person already has a `name` in `known_entities`, **reuse that name** (don't relabel) unless strong conflicting evidence. If current conflicts with prior, trust current and log in `visual.conflict`. If `none`, cold start.

================================================================
========== SUBTITLE REFERENCE (optional; authoritative) =========
================================================================
{{SUBTITLE}}

(The block above is self-contained: it states exactly how to use — or not use — the subtitle for TRANSCRIPT, SPEAKER, NAME grounding and paralinguistics in THIS clip. Follow it precisely; it overrides the generic wording of Part B where they differ.)

================================================================
============== PART A — VISUAL (top-priority rules) ==============
================================================================
**A1. Only what is truly visible; never hallucinate.** No carry-over of unseen objects/people. POV described objectively ("visible on screen ..."), not first person.
**A2. Hedge/omit easily-confused specifics** (colors, counts, identity, text, clock) unless sure; log in `visual.uncertain`.
**A3. Person-reference:** stable visual descriptors; no bare pronouns. (Canonical IDs/names in A7.)
**A4. No dialogue words here; DO record visible communicative behavior** (who speaks, faces whom, expression, gestures).
**A5. Density first; no filler; no hollow summaries.**

**A6. Sliding-window delta, de-dup & ACCUMULATIVE environment.** Sort what you see into 3 buckets:
- (i) already-stored & unchanged → suppress; list in `visual.duplicate_suppression`.
- (ii) newly-revealed DURABLE environment not in `scene_env_known` → `visual.scene_env_update` (even when `same_scene`; e.g. "whiteboard on left wall", "water dispenser in corner"). `scene_env_update` ≠ transient action.
- (iii) transient new happenings in target_range → `visual.target_range_delta`.
Also set `scene_continuity` (same_scene|new_scene|scene_transition|uncertain); on same_scene reuse prior `scene_id` and keep `visual_caption` focused on what's new. Set `memory_commit_recommendation` (create_event|extend_event|update_entity_state|no_op|uncertain).

**A7. Canonical entity alignment + NAME grounding.** For each person in `key_entities`:
- `person_id`: matched known id (P001…); or `null` + `match_status=new_entity` if clearly new; or `null` + `match_status=uncertain_candidate` if unsure. `candidate_ids` when uncertain. `match_confidence` (high/medium/low). `match_evidence` (e.g. "same scene + same clothing + continuous action", "same seat", "only person present"). `attribute_change` (compatible|conflict|not_observed|new_detail; don't overwrite stored attrs, just flag).
- **NAME grounding** — bind a real name to a person ONLY with explicit evidence; output `name` (or null), `name_confidence` (high/medium/low), `name_evidence`:
  - `self_introduction` — someone says "I'm X / my name is X" → that name belongs to the SPEAKER (cross-check who is speaking). HIGH.
  - `addressed_and_confirmed` — "are you X?" answered "yes", or repeatedly addressed as X and responds → that person. HIGH/MEDIUM.
  - `on_screen` — name tag / slide text. HIGH.
  - `mentioned_uncertain` — a name merely mentioned about someone possibly **absent** (e.g. "Mary will help") → do NOT bind to a visible person; set `name=null`, note candidate in `visual.uncertain`.
  - If `known_entities` already gives this person a name, reuse it (`name_evidence="prior"`).
  - Never bind a name just because it's the only name heard; require the link to be evidenced.

【VISUAL cover when present】 appearance; actions in temporal order; person-object/person-person interaction; key objects w/ state+location; space/setting; temporal cues.

================================================================
============== PART B — AUDIO (top-priority rules) ==============
================================================================
**B1. Verbatim transcription overrides all** — fillers/hesitations kept; no summarize/paraphrase/translate; original language; `[inaudible]` when unsure (never guess); no speech → `transcript`="" and `utterances`=[].
**B2. Paralinguistics from closed sets** (below); `unknown` if undeterminable (`none` for vocal_burst); judge from sound, not words.
**B3. Acoustics open-set** — `acoustic.events` short lowercase phrases ([] if none); `acoustic.scene` short phrase (`unknown` if undeterminable). Speech is not an acoustic event.
**B4. Transcribe first, label second.**
**B5. Timestamps required** — `start_sec`/`end_sec` absolute video seconds; basis in `audio.time_basis`="absolute_video_seconds"; `boundary_overlap` true if straddles context/target boundary. Build `transcript` as `[start-end] speaker: text` lines (use name or person_id if known, else descriptor).
**B6. Audio↔visual speaker alignment** — `speaker_id`=canonical person_id if identifiable else unknown/offscreen; keep `speaker_hint`; `alignment_confidence`; `alignment_evidence`. Don't force a binding.

【Paralinguistic closed sets】 emotion: neutral|happy|sad|angry|anxious|excited|tired|fearful|surprised|frustrated|affectionate|other|unknown · tone: calm|urgent|gentle|harsh|playful|serious|impatient|commanding|pleading|comforting|other|unknown · volume: whisper|low|normal|loud|shouting · rate: slow|normal|fast · pitch: low|normal|high · vocal_burst: laughing|crying|sighing|trembling|yawning|none
【voice_trait】 adult_male|adult_female|child|elderly_male|elderly_female|teen|unknown

================================================================
==================== OUTPUT (strict, single JSON) ===============
================================================================
Output ONLY the JSON below in one ```json fence. Exactly two top-level keys `visual`,`audio`. Cold-start defaults: `scene_continuity=new_scene`, `match_status=new_entity`, `person_id=null`, `name=null`, full initial environment listed in `scene_env_update`.

```json
{
  "visual": {
    "visual_caption": "<third-person; on same_scene focus on what's NEW in target_range; no dialogue words; flag uncertainties>",
    "scene_id": "<reused prior scene_id if same_scene; else null>",
    "scene_continuity": "same_scene | new_scene | scene_transition | uncertain",
    "scene_env_update": ["<durable env newly revealed, not in scene_env_known; on first clip list full initial env; [] if none>"],
    "key_entities": [
      {
        "ref": "<stable visual descriptor>",
        "person_id": "<P001 if matched; else null>",
        "match_status": "matched | new_entity | uncertain_candidate",
        "candidate_ids": ["<ids when uncertain_candidate; else empty>"],
        "match_confidence": "high | medium | low",
        "match_evidence": "<short reason>",
        "name": "<grounded real name, or null>",
        "name_confidence": "high | medium | low",
        "name_evidence": "self_introduction | addressed_and_confirmed | on_screen | prior | mentioned_uncertain | none",
        "type": "person | object | animal | other",
        "attributes": "<salient attrs, only if sure>",
        "attribute_change": "compatible | conflict | not_observed | new_detail",
        "state": "<current state/doing; '' if none>",
        "location": "<position; '' if undeterminable>"
      }
    ],
    "actions": ["<key actions temporal order; no dialogue words>"],
    "target_range_delta": ["<NEW transient happenings in target_range>"],
    "duplicate_suppression": ["<facts not re-committed because already stored>"],
    "memory_commit_recommendation": "create_event | extend_event | update_entity_state | no_op | uncertain",
    "place_hint": "<label or null>",
    "time_hint": "<cue or null>",
    "event_boundary_hint": {"multi_event": "<true/false>", "continues_prev": "<true/false>"},
    "conflict": ["<contradictions vs previous_state (trust current); [] if none>"],
    "uncertain": ["<uncertain visual points incl. unbound name candidates; [] if none>"]
  },
  "audio": {
    "transcript": "<'[start-end] speaker: text' lines, absolute seconds, temporal order; '' if no speech>",
    "time_basis": "absolute_video_seconds",
    "utterances": [
      {
        "speaker_id": "<person_id if identifiable; else unknown | offscreen>",
        "speaker_hint": "<descriptor / name / unknown speaker>",
        "alignment_confidence": "high | medium | low",
        "alignment_evidence": "<e.g. mouth movement overlaps speech | only visible speaker | offscreen voice>",
        "start_sec": "<abs seconds, number>",
        "end_sec": "<abs seconds, number>",
        "boundary_overlap": "<true/false>",
        "text": "<verbatim, original language, [inaudible] where unsure>",
        "lang": "zh | en | mixed | other",
        "paralinguistic": {"emotion": "<...>", "tone": "<...>", "volume": "<...>", "rate": "<...>", "pitch": "<...>", "vocal_burst": "<...>"}
      }
    ],
    "speakers": [{"speaker_id": "<person_id or unknown>", "speaker_hint": "<descriptor/name>", "voice_trait": "<set or unknown>"}],
    "acoustic": {"events": ["<non-speech phrases; [] if none>"], "scene": "<env phrase; unknown if undeterminable>"},
    "speaker_count_hint": "<int; 0 if no speech>",
    "inaudible_spans": "<int>"
  }
}
```

【Edge cases】 First clip: new_scene, full env in scene_env_update, persons new_entity/person_id null/name null unless self-intro present. No people: no person item. No speech: transcript "" + utterances []. Blurred/black: say so in visual_caption + uncertain.

【Example (illustrative; non-first clip; window=[40,70], context=[40,45], target=[45,70]; prior has S001, scene_env_known=["circuit-pattern wall","conference table"], P001(man,navy t-shirt,name=null), P002(woman,white blouse,name=null). In target a self-introduction is heard.】
```json
{
  "visual": {
    "visual_caption": "In target_range, P001 turns toward P002 and speaks while gesturing; P002 nods. No change to room layout.",
    "scene_id": "S001",
    "scene_continuity": "same_scene",
    "scene_env_update": [],
    "key_entities": [
      {"ref": "the man in a navy t-shirt", "person_id": "P001", "match_status": "matched", "candidate_ids": [], "match_confidence": "high", "match_evidence": "same scene + same navy t-shirt + same seat", "name": "Matthew", "name_confidence": "high", "name_evidence": "self_introduction", "type": "person", "attributes": "navy t-shirt,short dark hair", "attribute_change": "compatible", "state": "speaking toward P002", "location": "head of table"},
      {"ref": "the woman in a white blouse", "person_id": "P002", "match_status": "matched", "candidate_ids": [], "match_confidence": "high", "match_evidence": "same scene + same white blouse", "name": "Lucy", "name_confidence": "medium", "name_evidence": "addressed_and_confirmed", "type": "person", "attributes": "white blouse", "attribute_change": "compatible", "state": "nodding", "location": "near side of table"}
    ],
    "actions": ["P001 turns to P002 and speaks", "P002 nods"],
    "target_range_delta": ["P001 introduces himself to P002"],
    "duplicate_suppression": ["scene already stored (reused S001)", "appearance of P001/P002 already stored"],
    "memory_commit_recommendation": "extend_event",
    "place_hint": "meeting room",
    "time_hint": null,
    "event_boundary_hint": {"multi_event": false, "continues_prev": true},
    "conflict": [],
    "uncertain": []
  },
  "audio": {
    "transcript": "[45.2-47.0] Lucy: are you the new member, Matthew? [47.5-49.6] Matthew: hi, I'm Matthew.",
    "time_basis": "absolute_video_seconds",
    "utterances": [
      {"speaker_id": "P002", "speaker_hint": "Lucy", "alignment_confidence": "high", "alignment_evidence": "mouth movement overlaps speech", "start_sec": 45.2, "end_sec": 47.0, "boundary_overlap": false, "text": "are you the new member, Matthew?", "lang": "en", "paralinguistic": {"emotion": "neutral", "tone": "calm", "volume": "normal", "rate": "normal", "pitch": "normal", "vocal_burst": "none"}},
      {"speaker_id": "P001", "speaker_hint": "Matthew", "alignment_confidence": "high", "alignment_evidence": "mouth movement overlaps speech", "start_sec": 47.5, "end_sec": 49.6, "boundary_overlap": false, "text": "hi, I'm Matthew.", "lang": "en", "paralinguistic": {"emotion": "neutral", "tone": "gentle", "volume": "normal", "rate": "normal", "pitch": "normal", "vocal_burst": "none"}}
    ],
    "speakers": [{"speaker_id": "P001", "speaker_hint": "Matthew", "voice_trait": "adult_male"}, {"speaker_id": "P002", "speaker_hint": "Lucy", "voice_trait": "adult_female"}],
    "acoustic": {"events": [], "scene": "quiet meeting room"},
    "speaker_count_hint": 2,
    "inaudible_spans": 0
  }
}
```

Build environment accumulatively, suppress already-stored, surface newly-revealed env, and **ground names only with explicit evidence**. Commit new memory only from target_range. Output only the single JSON object above.
"""

# Induction: turn a batch of clips into semantic triples, merging against the keys already stored.
STAGE2_PROMPT = r"""You are a rigorous **multimodal knowledge inducer** for an entity-centric memory system. You are given (a) an ENTITY TABLE (canonical people/objects with ids + grounded names), (b) the EXISTING semantic memory accumulated so far (may be empty), and (c) a NEW BATCH of first-stage episodic memories (visual descriptions + verbatim audio with speaker & paralinguistic tags). Your task: **distill stable, reusable knowledge / preferences / relationships, and task-level aggregated facts**, as triples with a compact retrieval KEY, reconciling against existing memory. Output strict JSON.

================ TOP-PRIORITY RULES ================

**1. Induce stable knowledge & aggregates, do NOT retell one-off events.**
- Target: information that **still holds across time** — intrinsic attributes, role/identity, relationships, habits, **preferences/likes/dislikes**, and **task-level aggregates/decisions** (budgets, who-does-what, plans, scheduled things, required resources).
- A single transient action is NOT a triple ("opened a folder at 12:01" = episodic). A repeated/declared tendency IS ("prefers Chinese food", said he loves photography).

**2. Stable-from-one vs needs-repetition (avoid over-generalization).**
- Stable from a single clear statement (role, a stated preference, a self-introduced name, a decided budget number) → may output with `confidence=medium` (or `high` if explicit & unambiguous, e.g. "I'm Matthew", "prize: 3000 dollars").
- A *habit* claim ("usually/always") needs ≥2 evidences; with one instance, downgrade to a plain attribute/preference or omit.

**3. Evidence required; no hallucination; no mind-reading.**
- Every triple MUST be supported by the batch (or by updating an existing one); list supporting episodic ids (clip indices / timestamps) in `evidence`.
- No guessing motives. When uncertain, omit or mark `confidence=low`.

**4. Entity-centric subjects (use the ENTITY TABLE).**
- For people, set `subject` to the grounded **name** if known (else the descriptor) and `subject_id` to the canonical **person_id** (P00x). Keep consistent with the entity table.
- For task-level facts, use a stable event subject `subject="event:<short_name>"` (e.g. `event:promotion_campaign`), `subject_id` = same string.

**5. predicate ONLY from the ontology; type ONLY from the enum.**

**6. KEY convention** — `subject/short-category[/disambiguator]`, e.g. `Matthew/prefers/food`, `Annie/role`, `event:promotion_campaign/budget/prize`. Short, unique, readable; merge same subject+category under one key.

**7. Reconcile with EXISTING semantic memory (CRUD signals).**
- If this batch SUPPORTS an existing triple (same key/meaning) → `op="update"`, reuse its `key`, add new evidence (the driver will merge & may raise confidence).
- If NEW → `op="create"`.
- If this batch CONTRADICTS an existing triple (same subject+predicate, different object) → still output your best current triple, set `op="update"`, and set `conflicts_with` = the existing key, with a one-line `conflict_note`. (The driver resolves by confidence > evidence-count > recency; loser is soft-superseded.)
- Do not duplicate an unchanged existing triple that this batch says nothing new about — omit it.

==================================================================
【predicate ontology (English key = meaning)】
- attributes: `has_appearance` | `has_attribute` | `has_color`
- location: `usually_located_at` | `belongs_to_place` | `layout_is`
- possession: `owns` | `belongs_to`
- habit: `habitually_does` | `usually_at_time`
- preference: `prefers` | `dislikes` | `allergic_to`
- identity/ability: `role_is` | `can_do`
- relation: `relates_to` | `family_of` | `friend_of` | `colleague_of` | `manages` | `reports_to`
- task/event: `requires` | `budget_item` | `decided` | `plans_to` | `scheduled_at`
- fallback: `other` (clarify in statement)

【type enum】 `attribute` | `possession` | `location` | `habit` | `preference` | `identity` | `relation` | `task` | `decision` | `budget` | `other`

【ENTITY TABLE (canonical people/objects: id, name, appearance)】
{{ENTITY_TABLE}}

【EXISTING semantic memory (current triples; may be empty)】
{{EXISTING_SEMANTIC}}

【NEW BATCH of first-stage episodic memories (id = clip idx; audio lines are '[t][person_id/name][emotion/tone if salient] text')】
{{MEMORY_BATCH}}

【Output format: strict JSON in one ```json fence, only this object】
```json
{
  "triples": [
    {
      "key": "<compact stable retrieval key>",
      "subject": "<grounded name or stable descriptor; or event:...>",
      "subject_id": "<P00x / event:... ; null if unknown>",
      "predicate": "<an English key from the ontology>",
      "object": "<object/value>",
      "statement": "<one natural-language sentence for retrieval & reading>",
      "type": "<one of the type enum>",
      "confidence": "low | medium | high",
      "evidence": ["<supporting clip idx / timestamps from the batch (and existing evidence if updating)>"],
      "op": "create | update",
      "conflicts_with": "<existing key it contradicts, or null>",
      "conflict_note": "<one line if conflicts_with set, else empty>"
    }
  ]
}
```

【Edge cases】 If the batch yields no distillable stable knowledge: return `{"triples": []}`. Merge multiple batch evidences for the same knowledge into ONE triple. Prefer the grounded name (e.g. "Matthew") over a descriptor whenever the entity table provides it.

【Mini example (illustrative)】 Batch shows P001/Matthew saying he loves photography and (earlier) wanting Chinese food; Annie (P003) handed out materials & set the prize at 3000 dollars.
```json
{
  "triples": [
    {"key": "Matthew/prefers/hobby", "subject": "Matthew", "subject_id": "P001", "predicate": "prefers", "object": "photography", "statement": "Matthew likes photography (taking photos of people, scenery, animals).", "type": "preference", "confidence": "high", "evidence": ["54","55"], "op": "create", "conflicts_with": null, "conflict_note": ""},
    {"key": "Matthew/prefers/food", "subject": "Matthew", "subject_id": "P001", "predicate": "prefers", "object": "Chinese food", "statement": "Matthew prefers Chinese food.", "type": "preference", "confidence": "medium", "evidence": ["48"], "op": "create", "conflicts_with": null, "conflict_note": ""},
    {"key": "Annie/role", "subject": "Annie", "subject_id": "P003", "predicate": "role_is", "object": "team leader/manager", "statement": "Annie is the leader: hands out meeting materials and sets the agenda.", "type": "identity", "confidence": "high", "evidence": ["8","12"], "op": "create", "conflicts_with": null, "conflict_note": ""},
    {"key": "event:promotion_campaign/budget/prize", "subject": "event:promotion_campaign", "subject_id": "event:promotion_campaign", "predicate": "budget_item", "object": "prize money: 3000 USD", "statement": "The campaign's prize money budget is 3000 USD.", "type": "budget", "confidence": "high", "evidence": ["37"], "op": "create", "conflicts_with": null, "conflict_note": ""}
  ]
}
```

Induce only stable knowledge & task aggregates, ground subjects to entity ids/names, support every triple with evidence, reconcile with existing memory. Output only the JSON object above.
"""

# Rolling global summary (segment/merge). Maintained but not consumed — see DEVIATIONS.md.
GS_CONSOLIDATE_PROMPT = r"""You are maintaining a running GLOBAL SUMMARY (a compact "script") of a long video, consolidated hierarchically so recent detail and distant high-level context coexist within a bounded budget.

Task = {{KIND}}
- If "segment": the ITEMS are per-clip visual captions from ONE recent time window. Write ONE concise summary (1–3 sentences) of what happens in this window — the key people (keep concrete names/roles when present), their actions, notable events, and any setting change.
- If "merge": the ITEMS are several existing summaries of consecutive EARLIER windows. Compress them into ONE shorter, higher-level summary (1–2 sentences) that preserves the main events and their chronological order while dropping fine detail.

Rules: factual, third-person, no speculation; preserve temporal order; keep it SHORT (this is a bounded global context, not a transcript). Output ONLY the summary text — no JSON, no bullet points, no preamble.

ITEMS:
{{ITEMS}}
"""

# Name alignment: work out who is who by reading the cumulative transcript.
ALIGN_PROMPT_TRANSCRIPT = r"""You are resolving the character ROSTER of a long video's memory. A prior pass processed the
video clip-by-clip and detected people as ANONYMOUS entities (P001, P002, …) without binding
names. Your job: determine each entity's real name from the cumulative dialogue transcript.

You are given:
1. PERSONS — canonical people, each an id (P001, P002, …) + visual appearance description.
   NO names are shown — you derive them purely from dialogue evidence.
2. DIALOGUE — the full cumulative conversation in time order. Each line is labeled ONLY by the
   speaker's person id (P001, P002, …), NEVER by name. "offscreen" and "unknown" are NOT people:
   they cover any speech whose source is not a visible person (a voice off-camera, a device or
   assistant replying, a TV, an announcement).

Decide each person's name using ONLY in-dialogue evidence, in this PRIORITY order:
(a) SELF-INTRODUCTION (strongest): "I'm X" / "I am X" / "my name is X" → that speaker is X.
(b) VOCATIVE + RESPONSE: someone says "X, <request>" and the next responder is X.
    NOTE: a bare GREETING ("Hi X") is the WEAKEST vocative — trust only when nothing stronger contradicts.
(c) CHARACTERIZATION / ROLE DESCRIPTION: "X leads the meeting" → map to the person whose role matches.

CRITICAL RULES:
- **A name said BY a person to ADDRESS someone else is the LISTENER's name, never the speaker's.**
  Example: P001 says "Hello Emma" → "Emma" is NOT P001's name; it likely refers to whoever P001
  is talking to (check who responds next).
- **A word used to address a NON-PERSON is not a person name.** If someone addresses a device,
  an assistant or a voice off-camera and the reply comes from "offscreen" / "unknown" rather than
  from a person, the addressed word names that non-person — do NOT assign it to any person_id.
  This also covers mis-transcriptions of such a word into something that looks like a real name.
  Only bind a name when the one being addressed, introduced or described is a visible person.
- **COUNT THE PEOPLE.** Never assign the same name to two different person_ids.
- If TWO different names point to the SAME person, one is a mis-transcription. Keep the one with
  STRONGER evidence (more occurrences, clearer context).
- Do NOT invent names. If no clear evidence exists → name = null.
- This is an INCREMENTAL process — you may be called multiple times as the video progresses.
  Base your decision on ALL available evidence. If you previously assigned a name but new evidence
  contradicts it, correct it.

PERSONS:
{{ENTITY_TABLE}}

DIALOGUE (labeled by id only):
{{DIALOGUE}}

Output STRICT JSON only, in a ```json fence:
```json
{
  "roster": [
    {
      "person_id": "P001",
      "name": "Matthew",
      "confidence": "high",
      "evidence": "self-intro: 'Hi, I am Matthew' at 43s",
      "candidates": [
        {"name": "Matthew", "score": 0.9, "reason": "self-introduction"},
        {"name": "Matt", "score": 0.1, "reason": "possible nickname variant"}
      ]
    },
    {
      "person_id": "P002",
      "name": null,
      "confidence": "low",
      "evidence": "no reliable naming evidence yet",
      "candidates": []
    }
  ]
}
```
Rules for candidates:
- scores should sum to ~1.0 for each person (approximate is fine)
- include ALL plausible name candidates, even if score < 0.2
- "name" field = the top candidate (highest score), or null if no candidate scores >= 0.3
- confidence: "high" if top candidate score >= 0.7, "medium" if >= 0.4, "low" otherwise

Return a roster entry for EVERY person id listed in PERSONS. Output EXACTLY ONE json object.
Keep each "evidence" to ONE short sentence. One name maps to at most one person id.
"""
