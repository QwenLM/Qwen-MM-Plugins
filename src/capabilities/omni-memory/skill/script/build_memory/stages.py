"""The build-time stages: one clip in, memory state out.

pipeline.py drives these in order — extract a clip against the carried state, roll the batch up into
semantic triples, keep a global summary, align names off the accumulated transcript, then apply the
roster back over what was already written.

Nothing on the query side reaches any of it, which is why this file lives in the skill rather than in
the server package: omni_core carries the little that both halves need, llm.py the model calls, and
prompts.py the four prompts these stages fill in.

The caps below are read by this file alone, so they are defined here instead of being imported from
somewhere shared — the same reason NAME ALIGNMENT and GLOBAL SUMMARY are documented here now.
"""

import difflib
import json
import os
import re
import time

from llm import _stream_text, call_model
from omni_core import (
    ANON_NAMES,
    NAME_STOP,
    RESIDENT_ENTITY_CAP,
    SALIENT_EMO,
    SALIENT_TONE,
    diag,
    normalize_acoustic_events,
)
from prompts import ALIGN_PROMPT_TRANSCRIPT, GS_CONSOLIDATE_PROMPT, STAGE2_PROMPT, SW_PROMPT

NAME_LEDGER_CAP = 400  # streaming name-alignment: max distinct (name|speaker|next|type) mentions kept
SCENE_ENV_CAP = 20  # bounded scene_env; older items fall off instead of being LLM-deduplicated
GS_MERGE_K = int(os.environ.get("MEM_GS_MERGE_K", "6") or "6")  # global-state: merge oldest K nodes/level when >K
GS_RENDER_CAP = int(os.environ.get("MEM_GS_RENDER_CAP", "1200") or "1200")  # global-state render char cap

# ============================================================ GLOBAL SUMMARY
# A resolution-decaying rolling summary: each K-clip batch becomes a level-0 node, and once a level
# holds more than GS_MERGE_K nodes its oldest ones merge upwards. Recent detail and distant summary
# then coexist in a bounded budget, so it works on arbitrarily long streams. Built in the background
# rollup. Nothing consumes it today — see DEVIATIONS.md before extending it.

# ============================================================ NAME ALIGNMENT
# Per-clip extraction never binds a name, so names are resolved separately and can be revised as the
# video goes on. Three pieces:
#   ledger_accumulate                — per clip, no model call: tally who was called what.
#   infer_roster → apply_roster      — every K clips: hand the model the anonymous person table plus
#                                      the cumulative dialogue (ids only) and let it reason out who is
#                                      who. Correctable, with one final pass over the whole video.
#   name_of at query time           — answers show the current name, so old records need no rewrite.
# Model input grows with the number of people and a capped number of dialogue lines, NOT with video
# length.


# ============================================================ STAGE-1: STATEFUL DRIVER
# (ported 1:1 from run_memory_sliding.py — the model only *suggests*; the driver assigns IDs)
def new_state():
    return {
        "scene_id": None,
        "scene_summary": None,
        "scene_env": [],  # durable environment items, accumulated across clips
        "known_entities": [],  # [{person_id, name, appearance, last_location, last_action, ref}]
        "active_event_id": None,
        "active_event_summary": None,
        "last_processed_until": None,
        "active_audio_events": [],  # ongoing non-speech sounds at end of last clip (for continuity)
        "_scene_n": 0,
        "_entity_n": 0,
        "_event_n": 0,
    }


def prev_state_for_prompt(state, global_context=None):
    """Continuity-prior view handed to the model (internal counters hidden). `global_context` = the
    rolling global summary so far, given as background for extraction."""
    if state["scene_id"] is None and not state["known_entities"] and not global_context:
        return None
    d = {
        "scene_id": state["scene_id"],
        "scene_summary": state["scene_summary"],
        "scene_env_known": list(state.get("scene_env", [])),
        "known_entities": [
            {
                "person_id": e["person_id"],
                "name": e.get("name"),
                "appearance": e["appearance"],
                "last_location": e["last_location"],
                "last_action": e["last_action"],
            }
            for e in state["known_entities"]
        ],
        "active_event_id": state["active_event_id"],
        "active_event_summary": state["active_event_summary"],
        "last_processed_until": state["last_processed_until"],
    }
    if state.get("active_audio_events"):
        d["active_audio_events"] = state["active_audio_events"]
    if global_context:
        d["global_context"] = global_context
    return d


def _good_name(ent):
    """Only bind a real name when the dialogue evidence is strong enough."""
    nm = (ent.get("name") or "").strip()
    nc = (ent.get("name_confidence") or "").strip()
    ne = (ent.get("name_evidence") or "").strip()
    if nm and nc in ("high", "medium") and ne not in ("", "none", "mentioned_uncertain"):
        return nm
    return None


def update_state(state, parsed, win_end, caption_fallback=""):
    """Fold model output into canonical registries. Returns assignment record.

    When MEM_ANON_ENTITIES=1 (default ON): one-stage extraction does NOT bind names
    directly — entities stay anonymous (name=None). Name candidates are recorded as
    evidence for periodic alignment to resolve later. This prevents the "first-clip
    wrong name → permanent pollution" failure mode.
    """
    vis = (parsed or {}).get("visual", {}) if isinstance(parsed, dict) else {}
    assign = {"scene_id": None, "entity_map": [], "active_event_id": None, "name_candidates": []}
    _verify_prior = os.environ.get("MEM_PRIOR_VERIFY", "0") == "1"

    # ----- scene (freeze summary at creation; accumulate env; Q2) -----
    cont = (vis.get("scene_continuity") or "uncertain").strip()
    env_update = [s.strip() for s in (vis.get("scene_env_update") or []) if isinstance(s, str) and s.strip()]
    if cont in ("new_scene", "scene_transition") or state["scene_id"] is None:
        state["_scene_n"] += 1
        state["scene_id"] = f"S{state['_scene_n']:03d}"
        state["scene_summary"] = (vis.get("visual_caption") or caption_fallback or "")[:500]
        state["scene_env"] = []
    state.setdefault("scene_env", [])
    for it in env_update:
        if not any(it.lower() in e.lower() or e.lower() in it.lower() for e in state["scene_env"]):
            state["scene_env"].append(it)
    if len(state["scene_env"]) > SCENE_ENV_CAP:
        state["scene_env"] = state["scene_env"][-SCENE_ENV_CAP:]
    # accumulate acoustic.scene into scene_env (multimodal scene representation)
    aud = (parsed or {}).get("audio", {}) if isinstance(parsed, dict) else {}
    ac_scene = (aud.get("acoustic", {}) or {}).get("scene", "")
    if ac_scene and ac_scene not in ("unknown", ""):
        ac_tag = f"[sound] {ac_scene}"
        if not any(ac_tag.lower() in e.lower() or e.lower() in ac_tag.lower() for e in state["scene_env"]):
            state["scene_env"].append(ac_tag)
        if len(state["scene_env"]) > SCENE_ENV_CAP:
            state["scene_env"] = state["scene_env"][-SCENE_ENV_CAP:]
    assign["scene_id"] = state["scene_id"]

    # ----- entities (canonical id; name binding deferred when ANON mode) -----
    reg = {e["person_id"]: e for e in state["known_entities"]}
    for ent in vis.get("key_entities", []) or []:
        if (ent.get("type") or "") != "person":
            continue
        status = (ent.get("match_status") or "").strip()
        pid = ent.get("person_id")
        ref = ent.get("ref", "")
        nm = _good_name(ent)
        ne = (ent.get("name_evidence") or "").strip()
        nm_first = None if (_verify_prior and ne == "prior") else nm

        if ANON_NAMES:
            # Anonymous mode: record name candidate but do NOT write to entity
            if nm:
                assign["name_candidates"].append(
                    {
                        "person_id": pid,
                        "candidate_name": nm,
                        "name_evidence": ne,
                        "name_confidence": (ent.get("name_confidence") or "").strip(),
                        "timestamp": win_end,
                    }
                )
            bind_name = None
            # Also strip the name from key_entities so it cannot leak into the record —
            # the alignment pass is the ONLY writer of entity names.
            ent["name"] = None
        else:
            bind_name = nm_first

        if status == "matched" and pid in reg:
            r = reg[pid]
            r["last_location"] = ent.get("location") or r.get("last_location")
            r["last_action"] = ent.get("state") or r.get("last_action")
            if not ANON_NAMES and not r.get("name") and bind_name:
                r["name"] = bind_name
            if (ent.get("attribute_change") or "") == "new_detail" and ent.get("attributes"):
                extra = ent["attributes"]
                if extra and extra not in (r.get("appearance") or ""):
                    r["appearance"] = ((r.get("appearance") or "") + "; " + extra).strip("; ")
            assign["entity_map"].append({"ref": ref, "resolved": pid, "status": "matched", "name": r.get("name")})
        elif status == "new_entity":
            state["_entity_n"] += 1
            npid = f"P{state['_entity_n']:03d}"
            rec = {
                "person_id": npid,
                "name": bind_name,
                "appearance": ent.get("attributes", ""),
                "last_location": ent.get("location", ""),
                "last_action": ent.get("state", ""),
                "ref": ref,
            }
            state["known_entities"].append(rec)
            reg[npid] = rec
            if ANON_NAMES and nm:
                assign["name_candidates"][-1]["person_id"] = npid
            assign["entity_map"].append({"ref": ref, "resolved": npid, "status": "new_assigned", "name": bind_name})
        else:
            assign["entity_map"].append({"ref": ref, "resolved": None, "status": status or "uncertain"})

    # ----- active event -----
    rec = (vis.get("memory_commit_recommendation") or "uncertain").strip()
    if rec == "create_event":
        state["_event_n"] += 1
        state["active_event_id"] = f"E{state['_event_n']:03d}"
        delta = vis.get("target_range_delta") or []
        state["active_event_summary"] = ("; ".join(delta) if delta else (vis.get("visual_caption") or ""))[:300]
    elif rec == "extend_event":
        if state["active_event_id"] is None:
            state["_event_n"] += 1
            state["active_event_id"] = f"E{state['_event_n']:03d}"
        delta = vis.get("target_range_delta") or []
        if delta:
            state["active_event_summary"] = ("; ".join(delta))[:300]
    assign["active_event_id"] = state["active_event_id"]

    state["last_processed_until"] = win_end

    # update active_audio_events: extract ongoing sounds at clip end
    ac_events = normalize_acoustic_events((aud.get("acoustic", {}) or {}).get("events", []))
    ongoing = []
    for e in ac_events:
        if not isinstance(e, dict):
            continue
        end = e.get("end_sec")
        if end is not None and abs(end - win_end) < 6:
            ongoing.append(e.get("event", ""))
        elif e.get("continues_from_previous") and e.get("end_sec") is None:
            ongoing.append(e.get("event", ""))
    state["active_audio_events"] = [x for x in ongoing if x]

    return assign


def build_sw_prompt(win_start, win_end, ctx, tgt, prev_state):
    """Fill the sliding-window prompt's placeholders for one clip."""
    # The prompt carries a {{SUBTITLE}} slot left over from an oracle experiment that fed the
    # dataset's own .srt in as ground truth to measure an upper bound. There is no such file for a
    # real video, so the slot always gets the "transcribe it yourself" instruction.
    sub_block = (
        "none — no subtitle provided. Transcribe speech verbatim from the audio yourself "
        "(Part B), and ground names from evidence yourself (Part A7), as usual."
    )
    return (
        SW_PROMPT.replace("{{WINDOW_RANGE}}", f"[{win_start}, {win_end}] (absolute video seconds)")
        .replace(
            "{{CONTEXT_RANGE}}",
            "none (this is the first clip)" if ctx is None else f"[{ctx[0]}, {ctx[1]}] (absolute video seconds)",
        )
        .replace("{{TARGET_RANGE}}", f"[{tgt[0]}, {tgt[1]}] (absolute video seconds)")
        .replace(
            "{{PREVIOUS_STATE}}",
            "none (first clip; no continuity prior)"
            if prev_state is None
            else json.dumps(prev_state, ensure_ascii=False, indent=2),
        )
        .replace("{{SUBTITLE}}", sub_block)
    )


def extract_clip(client, clip, state, is_first=None):
    """Extract ONE clip statefully: previous_state → model → update_state → state_after.
    Returns (record, assignment). The record keeps previous_state/parsed/state_after — state_after is
    what a resumed build picks up from.
    is_first: whether this is the FIRST clip of the CURRENT segment (no overlap context);
    defaults to (idx == 0).

    Speaker attribution is done BY THE OMNI MODEL itself (prompt Part B6: it aligns each utterance to
    a canonical person_id using lip movement + who is visibly speaking). There is no acoustic
    diarization step."""
    idx, win_start, win_end = clip["idx"], clip["win_start"], clip["win_end"]
    overlap = clip.get("overlap", 5)
    first = (idx == 0) if is_first is None else bool(is_first)
    if first:
        ctx, tgt = None, [win_start, win_end]
    else:
        ctx = [win_start, round(win_start + overlap, 3)]
        tgt = [round(win_start + overlap, 3), win_end]
    ps = prev_state_for_prompt(state)
    prompt = build_sw_prompt(win_start, win_end, ctx, tgt, ps)
    t0 = time.time()
    res = call_model(client, clip["path"], prompt)
    elapsed = round(time.time() - t0, 2)
    rec = {
        "idx": idx,
        "clip": os.path.basename(clip["path"]).replace(".mp4", ""),
        "video_path": clip["path"],
        "win_start": win_start,
        "win_end": win_end,
        "window_range": [win_start, win_end],
        "context_range": ctx,
        "target_range": tgt,
        "previous_state": ps,
        "elapsed": elapsed,
    }
    if res.get("error"):
        rec.update(raw="", error=res["error"], parsed=None)
        return rec, None
    cap_fb = (
        (res.get("parsed") or {}).get("visual", {}).get("visual_caption", "")
        if isinstance(res.get("parsed"), dict)
        else ""
    )
    assign = update_state(state, res.get("parsed"), win_end, caption_fallback=cap_fb)
    rec.update(
        raw=res["raw"],
        parsed=res.get("parsed"),
        usage=res.get("usage"),
        attempts=res.get("attempts"),
        assignments=assign,
        state_after=json.loads(json.dumps(state)),
    )  # deep copy: the record keeps its own snapshot
    return rec, assign


def shift_record_time(rec, offset):
    """Add a global time offset (sec) to EVERY timestamp in an episodic record, in place.
    Used for incremental multi-upload so a long video's timeline stays continuous even
    though each segment is extracted with segment-local (0-based) times."""
    if not offset or not isinstance(rec, dict):
        return rec
    off = float(offset)
    for k in ("win_start", "win_end"):
        if isinstance(rec.get(k), (int, float)):
            rec[k] = round(rec[k] + off, 3)
    for k in ("window_range", "context_range", "target_range"):
        v = rec.get(k)
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            rec[k] = [round(v[0] + off, 3), round(v[1] + off, 3)]
    p = rec.get("parsed")
    if isinstance(p, dict) and isinstance(p.get("audio"), dict):
        for u in p["audio"].get("utterances") or []:
            for k in ("start_sec", "end_sec"):
                if isinstance(u.get(k), (int, float)):
                    u[k] = round(u[k] + off, 3)
    for sk in ("state_after", "previous_state"):
        sv = rec.get(sk)
        if isinstance(sv, dict) and isinstance(sv.get("last_processed_until"), (int, float)):
            sv["last_processed_until"] = round(sv["last_processed_until"] + off, 3)
    return rec


# ============================================================ STAGE-2: INDUCE + CRUD
CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _conf_rank(x):
    """Confidence comes from the model, so anything may show up — case variants, made-up words,
    None. Unknown ranks lowest rather than raising: a KeyError here happens on the background
    induction thread and would silently drop the whole batch of triples."""
    return CONF_RANK.get(str(x or "").strip().lower(), 0)


def _norm(x):
    return re.sub(r"\s+", " ", str(x or "").lower()).strip()


def fmt_entities(ents):
    out = [f"- {e['person_id']} name={e.get('name') or 'unknown'} :: {e.get('appearance', '')}" for e in ents]
    return "\n".join(out) or "(none)"


def fmt_batch(clips, id2name):
    """Compact time-ordered multimodal stream (audio lines with salient paralinguistic prefix)."""
    lines = []
    for o in clips:
        idx = o["idx"]
        p = o.get("parsed") or {}
        vis = p.get("visual", {}) or {}
        aud = p.get("audio", {}) or {}
        ents = [
            f"{e.get('person_id')}({e.get('name') or e.get('ref', '')})"
            for e in vis.get("key_entities", [])
            if e.get("type") == "person"
        ]
        lines.append(f"[clip {idx} | t={o.get('win_start')}-{o.get('win_end')}]")
        if vis.get("visual_caption"):
            lines.append(f"  VISUAL: {vis['visual_caption']}")
        delta = vis.get("target_range_delta") or []
        if delta:
            lines.append(f"  DELTA: {'; '.join(delta)}")
        if ents:
            lines.append(f"  ENTITIES: {', '.join(ents)}")
        utts = aud.get("utterances") or []
        if utts:
            lines.append("  AUDIO:")
            for u in utts:
                sid = u.get("speaker_id") or "unknown"
                nm = id2name.get(sid, "")
                who = f"{sid}/{nm}" if nm else sid
                pl = u.get("paralinguistic") or {}
                tags = []
                if (pl.get("emotion") or "") not in SALIENT_EMO:
                    tags.append(pl["emotion"])
                if (pl.get("tone") or "") not in SALIENT_TONE:
                    tags.append(pl["tone"])
                tg = f"[{','.join(tags)}]" if tags else ""
                lines.append(f"    [t={u.get('start_sec')}][{who}]{tg} {u.get('text', '')}")
    return "\n".join(lines)


def _fmt_store(store):
    if not store:
        return "(empty)"
    out = [
        f"- {k} | {t['subject']} {t['predicate']} {t['object']} "
        f"(conf={t['confidence']}, ev={len(t.get('evidence', []))})"
        for k, t in store.items()
        if t.get("status") != "superseded"
    ]
    return "\n".join(out) or "(empty)"


def _resolve(store, superseded, ex, t):
    """Keep winner by confidence > evidence-count > recency(new). Loser -> superseded."""
    a = (_conf_rank(ex.get("confidence")), len(ex.get("evidence", [])), 0)
    b = (_conf_rank(t.get("confidence")), len(t.get("evidence", [])), 1)
    win, lose = (t, ex) if b >= a else (ex, t)
    lose = dict(lose)
    lose["status"] = "superseded"
    lose["superseded_by"] = win.get("key")
    superseded.append(lose)
    win["evidence"] = sorted(set(ex.get("evidence", [])) | set(t.get("evidence", [])), key=str)
    win["status"] = "active"
    store[win["key"]] = win


def merge_triple(store, t, superseded):
    """Driver-side CRUD by key + conflict rules. Returns 'create'|'update'|'conflict'|'skip'."""
    k = t.get("key")
    if not k:
        return "skip"
    t.setdefault("status", "active")
    ev = set(t.get("evidence") or [])
    if k not in store:
        cw = t.get("conflicts_with")
        if cw and cw in store:
            _resolve(store, superseded, store[cw], t)
            return "conflict"
        t["evidence"] = sorted(ev, key=str)
        store[k] = t
        return "create"
    ex = store[k]
    if _norm(ex.get("object")) == _norm(t.get("object")):
        ex["evidence"] = sorted(set(ex.get("evidence", [])) | ev, key=str)
        if _conf_rank(t.get("confidence")) > _conf_rank(ex.get("confidence")):
            ex["confidence"] = t["confidence"]
        if len(ex["evidence"]) >= 2 and ex.get("confidence") == "medium" and ex.get("type") in ("habit", "preference"):
            ex["confidence"] = "high"
        return "update"
    _resolve(store, superseded, ex, t)
    return "conflict"


def stage2_rollup(client, entities, batch, sem_store, superseded):
    """ONE incremental rollup: induce triples over `batch` given the current entity table +
    existing sem_store, then merge (in-place) into sem_store/superseded. Returns ops stats.
    Safe to run in a background worker: it only reads its args and writes sem_store/superseded
    (which the caller must keep single-writer)."""
    id2name = {e["person_id"]: (e.get("name") or "") for e in entities}
    prompt = (
        STAGE2_PROMPT.replace("{{ENTITY_TABLE}}", fmt_entities(entities))
        .replace("{{EXISTING_SEMANTIC}}", _fmt_store(sem_store))
        .replace("{{MEMORY_BATCH}}", fmt_batch(batch, id2name))
    )
    res = _stream_text(client, prompt)
    ops = {
        "create": 0,
        "update": 0,
        "conflict": 0,
        "tokens": (res.get("usage") or {}).get("total_tokens", 0),
        "calls": 1,
    }
    parsed = res.get("parsed")
    if res.get("error") or not isinstance(parsed, dict):
        return ops
    for t in parsed.get("triples") or []:
        op = merge_triple(sem_store, t, superseded)
        if op in ops:
            ops[op] += 1
    return ops


def stage2_finalize(client, sem_store):
    """Merge near-duplicates across the whole active store. Returns (list, tokens);
    the list always has status/embedding defaults set (never None)."""
    active = [v for v in sem_store.values() if v.get("status") != "superseded"]
    consolidated, tok = _finalize_semantic(client, active)
    if consolidated is None:
        consolidated = active
    for t in consolidated:
        t.setdefault("status", "active")
        t.setdefault("embedding", None)
    return consolidated, tok


def _finalize_semantic(client, active_list):
    """Final consolidation pass (merge near-dups). Returns (list_or_None, tokens)."""
    if not active_list:
        return [], 0
    instr = (
        "You are consolidating an entity-centric semantic memory. Below are triples (some may be "
        "near-duplicates or mergeable). Merge duplicates/synonyms (same subject+predicate+meaning) "
        "into one, keep the clearest statement, union their evidence, keep the highest confidence, "
        "and drop none that carry distinct knowledge. Do NOT invent new facts. Output strict JSON "
        '{"triples":[...]} with the same fields (key, subject, subject_id, predicate, object, '
        "statement, type, confidence, evidence).\n\nTRIPLES:\n"
        + json.dumps(active_list, ensure_ascii=False, indent=2)
        + "\n\nOutput only the JSON object in a ```json fence."
    )
    res = _stream_text(client, instr)
    tok = (res.get("usage") or {}).get("total_tokens", 0)
    if res.get("parsed") and isinstance(res["parsed"].get("triples"), list):
        return res["parsed"]["triples"], tok
    return None, tok


def _gs_summarize(client, items, kind):
    """Compress a list of texts into ONE concise summary. kind='segment' (K clip captions) or
    'merge' (K node summaries). Returns text ('' on failure)."""
    body = "\n".join(f"- {t}" for t in items if str(t or "").strip())
    if not body:
        return ""
    prompt = GS_CONSOLIDATE_PROMPT.replace("{{KIND}}", kind).replace("{{ITEMS}}", body)
    try:
        res = _stream_text(client, prompt)
        return (res.get("raw") or "").strip()
    except Exception:
        return ""


def _gs_cascade_merge(client, nodes):
    """One upward pass: at each level with > GS_MERGE_K nodes, merge its oldest GS_MERGE_K into one
    node at level+1 (so every level keeps ≤ GS_MERGE_K → bounded). Returns a NEW chronological list."""
    nodes = list(nodes)
    lvl, max_lvl = 0, max((n.get("level", 0) for n in nodes), default=0)
    while lvl <= max_lvl:
        at = sorted((n for n in nodes if n.get("level", 0) == lvl), key=lambda n: n.get("t0") or 0)
        if len(at) > GS_MERGE_K:
            oldest = at[:GS_MERGE_K]
            text = _gs_summarize(client, [n.get("text", "") for n in oldest], "merge")
            oid = {id(n) for n in oldest}
            nodes = [n for n in nodes if id(n) not in oid]
            nodes.append({"level": lvl + 1, "text": text, "t0": oldest[0].get("t0"), "t1": oldest[-1].get("t1")})
            max_lvl = max(max_lvl, lvl + 1)
        lvl += 1
    nodes.sort(key=lambda n: n.get("t0") or 0)
    return nodes


def gs_render_nodes(nodes, cap=None):
    """Render nodes chronologically (older/coarse → recent/fine); tail-capped to `cap` chars."""
    if not nodes:
        return ""
    cap = cap or GS_RENDER_CAP
    ns = sorted(nodes, key=lambda n: n.get("t0") or 0)
    text = " ".join(
        f"[{int(n.get('t0') or 0)}-{int(n.get('t1') or 0)}s] {n.get('text', '')}" for n in ns if n.get("text")
    )
    return text[-cap:] if len(text) > cap else text


def gs_add_segment(client, store, batch):
    """Add ONE level-0 node summarizing a K-clip batch, then cascade-merge. Mutates
    store.global_nodes / store.global_summary via ATOMIC reassignment (race-safe vs the main-thread
    snapshot). Runs in the background rollup thread."""
    caps = []
    for rec in batch:
        vis = (rec.get("parsed") or {}).get("visual", {}) or {}
        cap = (vis.get("visual_caption") or "").strip()
        if cap:
            caps.append(f"[{rec.get('win_start')}-{rec.get('win_end')}s] {cap}")
    text = _gs_summarize(client, caps, "segment")
    if not text:
        return
    node = {"level": 0, "text": text, "t0": batch[0].get("win_start"), "t1": batch[-1].get("win_end")}
    nodes = _gs_cascade_merge(client, list(store.global_nodes) + [node])
    store.global_nodes = nodes  # atomic swap
    store.global_summary = gs_render_nodes(nodes)  # keep field in sync (QA-resident / view)


def gs_flush(store):
    """Finalize: recompute the rendered global_summary from current nodes (no LLM)."""
    store.global_summary = gs_render_nodes(store.global_nodes)
    return store.global_summary


# name token: no apostrophe → "Emma's" yields "Emma", "It's"/"Don't" don't leak in as names
_NAME_TOK = re.compile(r"\b([A-Z][a-z]{2,})\b")


def _wb_replace(text, old, new):
    if not text or not old or old.lower() == "unknown":
        return text
    return re.sub(r"\b" + re.escape(old) + r"\b", new, text)


def _extract_roster(raw):
    """Robustly pull the roster: the model may reason at length then emit the FINAL json; take the
    LAST ```json block that contains a roster (avoids grabbing a mid-draft that mis-assigns names)."""
    fences = re.findall(r"```json\s*(.*?)```", raw or "", re.DOTALL) or ([raw] if raw else [])
    for c in reversed(fences):
        for cand in (c, c[c.find("{") : c.rfind("}") + 1] if "{" in c else ""):
            try:
                d = json.loads(cand)
            except Exception:
                continue
            if isinstance(d, dict) and isinstance(d.get("roster"), list):
                return d
    return None


def _person_ids(store):
    """All appearing person ids (from entities + any P0xx seen in key_entities)."""
    ids = [e.get("person_id") for e in store.entities if e.get("person_id")]
    seen = set(ids)
    for o in store.episodic:
        p = o.get("parsed") or {}
        for ent in (p.get("visual", {}) or {}).get("key_entities", []) or []:
            pid = ent.get("person_id")
            if pid and pid.startswith("P") and pid not in seen:
                seen.add(pid)
                ids.append(pid)
    return ids


def build_persons_view(store):
    """PERSONS block: id + appearance + #lines + role clues (semantic predicate=object, NO names).
    Over RESIDENT_ENTITY_CAP → keep most freq×recent (bounded). name_locked persons are annotated
    with their current name so the LLM keeps it (apply_roster also enforces locks)."""
    appearance = {e.get("person_id"): (e.get("appearance") or "") for e in store.entities}
    locked = {e.get("person_id"): e.get("name") for e in store.entities if e.get("name_locked") and e.get("name")}
    utt_n = {}
    for o in store.episodic:
        for u in ((o.get("parsed") or {}).get("audio", {}) or {}).get("utterances", []) or []:
            sid = u.get("speaker_id")
            if sid:
                utt_n[sid] = utt_n.get(sid, 0) + 1
    roles = {}
    for t in store.semantic:
        sid = t.get("subject_id")
        if sid and str(sid).startswith("P"):
            roles.setdefault(sid, []).append(f"{t.get('predicate')}={t.get('object')}")
    pids = _person_ids(store)
    if len(pids) > RESIDENT_ENTITY_CAP:  # bounded: keep active people only
        byid = {e.get("person_id"): e for e in store.entities}
        pids = sorted(
            pids,
            key=lambda p: (byid.get(p, {}).get("freq", 0) or 0, byid.get(p, {}).get("last_used", 0) or 0),
            reverse=True,
        )[:RESIDENT_ENTITY_CAP]
    lines = []
    for pid in pids:
        r = "; ".join(roles.get(pid, [])[:6]) or "(none)"
        line = (
            f"{pid} | appearance: {appearance.get(pid, '') or '(unknown)'} | "
            f"speaks {utt_n.get(pid, 0)} lines | clues: {r}"
        )
        if pid in locked:
            line += f"  [LOCKED name={locked[pid]} — keep, do not change or reuse]"
        lines.append(line)
    return "\n".join(lines) or "(no persons)"


def build_dialogue_view(store, max_lines=400, recent_clips=None):
    """DIALOGUE block: utterances in time order, labeled ONLY by speaker_id (never the name hint,
    which may be a propagated wrong label). recent_clips=N → only the last N episodic records
    (bounded, for streaming); else all clips, evenly sampled to max_lines."""
    eps = store.episodic[-recent_clips:] if recent_clips else store.episodic
    rows = []
    for o in eps:
        aud = (o.get("parsed") or {}).get("audio", {}) or {}
        for u in aud.get("utterances", []) or []:
            txt = (u.get("text") or "").strip()
            if not txt:
                continue
            t = u.get("start_sec")
            t = t if isinstance(t, (int, float)) else o.get("win_start", 0)
            rows.append((float(t), u.get("speaker_id") or "unknown", txt))
    rows.sort(key=lambda x: x[0])
    if len(rows) > max_lines:
        step = len(rows) / max_lines
        rows = [rows[int(i * step)] for i in range(max_lines)]
    return "\n".join(f"[{t:.1f}] {sid}: {txt}" for t, sid, txt in rows) or "(no dialogue)"


# ---------- L1: deterministic evidence ledger (per clip, 0 LLM) ----------
def _classify_mention(text, nm):
    """Classify how a spoken name appears (drives evidence strength)."""
    low = text.lower()
    n = re.escape(nm.lower())
    # self-intro: only strong first-person patterns. Dropped "this is X" — it mis-fires on
    # "this is Emma's book" (possession, NOT self-introduction), which wrongly binds Emma to
    # the speaker. (?!'s) also guards against possessive forms.
    if re.search(r"\b(i'?m|i am|my name is)\s+" + n + r"\b(?!'s)", low):
        return "self_intro"
    if re.search(r"\b(hi|hello|hey|thanks|thank you)\s+" + n + r"\b(?!'s)", low):
        return "greeting"  # weakest (easily mis-heard)
    # vocative (address): name at clause start / end, or right after a discourse marker
    # ("well Jack, …", "okay, Emma, …") — the mid-clause case matters for turn-taking direction.
    _lead = r"(?:^|[,.!?]|\b(?:well|oh|so|okay|ok|yes|yeah|no|listen|look|sorry|please|hmm)\b)"
    if (
        re.search(r"^\s*" + n + r"\s*[,:]", low)
        or re.search(r",\s*" + n + r"\s*[?.!]*\s*$", low)
        or re.search(_lead + r"[\s,]+" + n + r"\s*[,?.!:]", low)
    ):
        return "vocative"
    return "described"


def rows_from_records(records):
    """episodic record(s) → time-ordered [(t, speaker_id, text, next_speaker_id)] for the ledger."""
    tmp = []
    for o in records:
        for u in ((o.get("parsed") or {}).get("audio", {}) or {}).get("utterances", []) or []:
            txt = (u.get("text") or "").strip()
            if not txt:
                continue
            t = u.get("start_sec")
            t = t if isinstance(t, (int, float)) else o.get("win_start", 0)
            tmp.append((float(t), u.get("speaker_id") or "unknown", txt))
    tmp.sort(key=lambda x: x[0])
    return [(t, sid, txt, (tmp[i + 1][1] if i + 1 < len(tmp) else None)) for i, (t, sid, txt) in enumerate(tmp)]


def ledger_accumulate(ledger, rows, cap=NAME_LEDGER_CAP):
    """Fold a batch of new utterances into the ledger (in place). key='name|speaker|next|type';
    value={count, example, first_t, last_t, evidence: [{t, text}]}. Bounded.

    Evidence-based upgrade: each entry now carries a deduplicated evidence list with timestamps.
    Overlap dedup: if the same speaker says highly similar text within 6s (sliding window overlap),
    the evidence is NOT counted again — prevents confidence inflation from overlapping clips."""
    _OVERLAP_WINDOW = 6.0
    for t, sid, text, nxt in rows:
        if not text:
            continue
        for m in _NAME_TOK.finditer(text):
            nm = m.group(1)
            if nm.lower() in NAME_STOP:
                continue
            mtype = _classify_mention(text, nm)
            key = f"{nm}|{sid or 'unknown'}|{nxt or 'none'}|{mtype}"
            e = ledger.get(key)
            if e is None:
                ledger[key] = {
                    "count": 1,
                    "example": text[:120],
                    "first_t": float(t),
                    "last_t": float(t),
                    "evidence": [{"t": float(t), "text": text[:120]}],
                }
            else:
                # Overlap dedup: skip if same speaker, highly overlapping time, similar text
                is_dup = False
                for ev in e.get("evidence", []):
                    if abs(ev["t"] - float(t)) < _OVERLAP_WINDOW:
                        sim = difflib.SequenceMatcher(None, ev["text"][:60], text[:60]).ratio()
                        if sim > 0.7:
                            is_dup = True
                            break
                if not is_dup:
                    e["count"] += 1
                    e["last_t"] = float(t)
                    ev_list = e.setdefault("evidence", [])
                    ev_list.append({"t": float(t), "text": text[:120]})
                    if len(ev_list) > 20:
                        ev_list[:] = ev_list[-20:]
    if len(ledger) > cap:
        keep = dict(sorted(ledger.items(), key=lambda kv: -kv[1]["count"])[:cap])
        ledger.clear()
        ledger.update(keep)
    return ledger


def ledger_accumulate_candidates(ledger, candidates, cap=NAME_LEDGER_CAP):
    """Fold name_candidates from update_state (anonymous entity mode) into the ledger.
    Each candidate: {person_id, candidate_name, name_evidence, name_confidence, timestamp}.
    Creates synthetic ledger entries with type derived from name_evidence."""
    _EVIDENCE_TO_TYPE = {
        "self_introduction": "self_intro",
        "addressed_and_confirmed": "vocative",
        "on_screen": "described",
        "prior": "described",
        "mentioned_uncertain": "described",
    }
    _OVERLAP_WINDOW = 6.0
    for cand in candidates:
        nm = (cand.get("candidate_name") or "").strip()
        pid = cand.get("person_id") or "unknown"
        ne = (cand.get("name_evidence") or "").strip()
        t = float(cand.get("timestamp", 0))
        if not nm or nm.lower() in NAME_STOP:
            continue
        mtype = _EVIDENCE_TO_TYPE.get(ne, "described")
        key = f"{nm}|{pid}|none|{mtype}"
        e = ledger.get(key)
        if e is None:
            ledger[key] = {
                "count": 1,
                "example": f"[entity evidence: {ne}]",
                "first_t": t,
                "last_t": t,
                "evidence": [{"t": t, "text": f"[{ne}] {nm} for {pid}"}],
            }
        else:
            is_dup = any(abs(ev["t"] - t) < _OVERLAP_WINDOW for ev in e.get("evidence", []))
            if not is_dup:
                e["count"] += 1
                e["last_t"] = t
                ev_list = e.setdefault("evidence", [])
                ev_list.append({"t": t, "text": f"[{ne}] {nm} for {pid}"})
                if len(ev_list) > 20:
                    ev_list[:] = ev_list[-20:]
    if len(ledger) > cap:
        keep = dict(sorted(ledger.items(), key=lambda kv: -kv[1]["count"])[:cap])
        ledger.clear()
        ledger.update(keep)
    return ledger


# ---------- apply / propagate (shared by streaming + one-shot) ----------
_CONF_RANK_NAME = {"high": 3, "medium": 2, "low": 1, "": 0}


def propagate_names(store, changes):
    """Rewrite confirmed renames into key_entities / utterances&speakers / semantic / caption / text.
    This is the 'rewrite history' part — bounded videos call it once; streaming defers it to finalize
    (relying on L3 query-time mapping meanwhile)."""
    if not changes:
        return
    chg = {c["pid"]: c for c in changes}
    corr = [(c["old"], c["new"]) for c in changes if c["old"]]  # confirmed corrections (old→new)

    def _fix(s):
        for old, new in corr:
            s = _wb_replace(s, old, new)
        return s

    for o in store.episodic:
        vis = (o.get("parsed") or {}).get("visual", {}) or {}
        for e in vis.get("key_entities", []) or []:
            if e.get("person_id") in chg:
                e["name"] = chg[e["person_id"]]["new"]
        if corr:
            if vis.get("visual_caption"):
                vis["visual_caption"] = _fix(vis["visual_caption"])
            vis["actions"] = [_fix(x) for x in (vis.get("actions") or [])]
            vis["target_range_delta"] = [_fix(x) for x in (vis.get("target_range_delta") or [])]
        aud = (o.get("parsed") or {}).get("audio", {}) or {}
        if corr and aud.get("transcript"):
            aud["transcript"] = _fix(aud["transcript"])
        for u in aud.get("utterances", []) or []:
            if u.get("speaker_id") in chg:
                u["speaker_hint"] = chg[u["speaker_id"]]["new"]
            if corr and u.get("text"):
                u["text"] = _fix(u["text"])
        for s in aud.get("speakers", []) or []:
            if s.get("speaker_id") in chg:
                s["speaker_hint"] = chg[s["speaker_id"]]["new"]
    for t in store.semantic:
        sid = t.get("subject_id")
        if sid in chg:
            t["subject"] = chg[sid]["new"]
        for c in changes:
            if c["old"]:
                t["statement"] = _wb_replace(t.get("statement", ""), c["old"], c["new"])
                t["object"] = _wb_replace(t.get("object", ""), c["old"], c["new"])


def _sync_driver_state_names(store):
    """Mirror entity name/name_locked into driver_state.known_entities (name is stored there too;
    append continuation restores state0 from it, so it must not carry stale names)."""
    ds = store.driver_state
    if not isinstance(ds, dict):
        return
    byid = {e.get("person_id"): e for e in store.entities}
    for e in ds.get("known_entities", []) or []:
        se = byid.get(e.get("person_id"))
        if se:
            if se.get("name") is not None:
                e["name"] = se.get("name")
            if se.get("name_locked"):
                e["name_locked"] = True


def _name_evidence(ledger, pid, name):
    """Count support vs oppose evidence for name relative to pid.
    Ledger key format: Name|speaker_id|next_speaker_id|type.
    - self_intro BY pid → support (pid introduced themselves as this name)
    - vocative/greeting BY pid → oppose (pid called SOMEONE ELSE this name)
    - vocative/greeting TO pid (nxt==pid) → support (someone called pid this name)
    - described where nxt==pid → weak support
    Returns (support_count, oppose_count)."""
    nl = (name or "").lower()
    support = oppose = 0
    off = {"offscreen", "unknown", "none", "", None}
    for key in ledger:
        parts = key.split("|")
        if len(parts) != 4:
            continue
        nm, sid, nxt, mtype = parts
        if nm.lower() != nl:
            continue
        cnt = ledger[key].get("count", 1)
        if sid == pid:
            if mtype == "self_intro":
                support += cnt
            elif mtype in ("vocative", "greeting"):
                oppose += cnt
        if nxt == pid and nxt not in off and sid != pid:
            if mtype in ("vocative", "greeting"):
                support += cnt
            elif mtype == "described":
                support += cnt // 2 + 1
    return support, oppose


def apply_roster(store, roster, *, respect_lock=True, allow_override=True, propagate="entities"):
    """Apply an LLM roster to the store. Returns changes [{pid, old, new, conf}].

    v2 upgrade: processes candidate distributions from the v2 alignment prompt.
    Rule-based resolution layer:
    1. Self-introduction evidence → highest priority
    2. Vocative evidence creates negative evidence (speaker is NOT that name)
    3. Described/mentioned → lower weight
    4. Candidate score threshold: top candidate must score >= 0.3 to bind
    5. Name collision: one name → one person

    Priority: VETO > LOCK > candidate-score-threshold > collision > override policy."""
    by_id = {e.get("person_id"): e for e in store.entities}

    def _rank(pid):
        r = next((x for x in (roster or []) if x.get("person_id") == pid), {})
        return _CONF_RANK_NAME.get((r.get("confidence") or "").lower(), 0)

    def _top_score(r):
        """Get top candidate score from roster entry (v2 format)."""
        cands = r.get("candidates") or []
        if not cands:
            return 1.0
        return max((c.get("score", 0) for c in cands), default=0)

    # roster → want (high/medium only, with candidate score check)
    want = {}
    for r in roster or []:
        pid = r.get("person_id")
        nm = (r.get("name") or "").strip()
        conf = (r.get("confidence") or "").strip().lower()
        if not pid or not nm or conf not in ("high", "medium"):
            continue
        top_score = _top_score(r)
        if top_score < 0.3:
            diag(f"[MEM] ▸ NAME-LOWSCORE {pid} skip '{nm}' (score={top_score:.2f} < 0.3)", flush=True)
            continue
        want[pid] = {
            "name": nm,
            "conf": conf,
            "evidence": (r.get("evidence") or ""),
            "score": top_score,
            "candidates": r.get("candidates") or [],
        }

    # HARD CHECK — veto disabled in ANON mode (speaker attribution errors cause too many false kills;
    # ANON mode already prevents stage-1 wrong binding, making veto redundant double-insurance that
    # does more harm than good). In non-ANON mode, use weakened count-based veto.
    led = getattr(store, "name_ledger", None) or {}
    if led and not ANON_NAMES:
        for pid in list(want):
            nm = want[pid]["name"]
            support, oppose = _name_evidence(led, pid, nm)
            should_veto = support == 0 and oppose >= 3
            if should_veto:
                # In v2 mode, check if there's a second candidate to fall back to
                fallback = None
                for cand in want[pid].get("candidates", []):
                    cn = (cand.get("name") or "").strip()
                    if cn and cn.lower() != nm.lower() and cand.get("score", 0) >= 0.3:
                        s2, o2 = _name_evidence(led, pid, cn)
                        if not (s2 == 0 and o2 >= 3):
                            fallback = {
                                "name": cn,
                                "score": cand["score"],
                                "conf": "medium",
                                "evidence": cand.get("reason", ""),
                            }
                            break
                if fallback:
                    diag(f"[MEM] ▸ NAME-VETO {pid} ✗ '{nm}' → fallback '{fallback['name']}'", flush=True)
                    want[pid].update(
                        name=fallback["name"],
                        conf=fallback["conf"],
                        evidence=fallback["evidence"],
                        score=fallback["score"],
                    )
                else:
                    want.pop(pid)
                    ent = by_id.get(pid)
                    if ent and (ent.get("name") or "").lower() == nm.lower():
                        ent["name"] = None
                        ent.pop("name_locked", None)
                    diag(f"[MEM] ▸ NAME-VETO {pid} ✗ '{nm}' (only used BY {pid} to address others)", flush=True)

    # LOCK: skip locked persons; reserve their names
    if respect_lock:
        locked_owner = {
            e["name"].lower(): e.get("person_id") for e in store.entities if e.get("name_locked") and e.get("name")
        }
        for pid in list(want):
            e = by_id.get(pid)
            if e and e.get("name_locked"):
                want.pop(pid)
                continue
            owner = locked_owner.get(want[pid]["name"].lower())
            if owner is not None and owner != pid:
                want.pop(pid)

    # COLLISION: one name → one person (highest score wins, then conf)
    # Also check against EXISTING entity names (not just within this roster round)
    existing_names = {}
    for e in store.entities:
        en = e.get("name")
        if en and e.get("person_id") not in want:
            existing_names.setdefault(en.lower(), []).append(e["person_id"])

    name2pids = {}
    for pid, w in want.items():
        name2pids.setdefault(w["name"].lower(), []).append(pid)
    for nm, pids in name2pids.items():
        all_holders = pids + existing_names.get(nm, [])
        if len(all_holders) > 1:

            def _collision_key(p):
                if p in want:
                    return (want[p].get("score", 0), _rank(p))
                return (0.5, 2)

            best = max(all_holders, key=_collision_key)
            for p in pids:
                if p != best:
                    diag(f"[MEM] ▸ NAME-COLLISION '{nm}' {p} loses to {best}", flush=True)
                    want.pop(p, None)
            for p in existing_names.get(nm, []):
                if p != best:
                    ent = by_id.get(p)
                    if ent:
                        diag(f"[MEM] ▸ NAME-COLLISION '{nm}' existing {p} cleared (new winner={best})", flush=True)
                        ent["name"] = None

    # OVERRIDE POLICY + write entity names
    changes = []
    for pid, w in want.items():
        ent = by_id.get(pid)
        old = (ent.get("name") if ent else None) or None
        nm, conf = w["name"], w["conf"]
        lock_now = False  # never lock a name mid-stream; finalize decides
        if old == nm:
            if ent is not None and lock_now:
                ent["name_locked"] = True
            continue
        if old is not None and not allow_override:
            continue
        changes.append({"pid": pid, "old": old, "new": nm, "conf": conf})
        if ent is not None:
            ent["name"] = nm
            if lock_now:
                ent["name_locked"] = True

    if changes:
        if propagate == "full":
            propagate_names(store, changes)
        _sync_driver_state_names(store)
    store.set_entities(store.entities)
    return changes


# ---------- periodic name alignment from the cumulative transcript ----------
def build_transcript_align_prompt(store, max_lines=500):
    """Compose the transcript-based alignment prompt: persons (no names) + cumulative dialogue
    (all clips, no windowing). Deliberately does NOT use the ledger digest — it relies entirely on
    the model reasoning over the raw transcript to infer who is who.
    Output carries a candidate distribution per person (see apply_roster)."""
    persons = build_persons_view(store)
    dialogue = build_dialogue_view(store, max_lines=max_lines, recent_clips=None)
    return ALIGN_PROMPT_TRANSCRIPT.replace("{{ENTITY_TABLE}}", persons).replace("{{DIALOGUE}}", dialogue)


def infer_roster(client, prompt):
    """Slow part: one LLM call → roster list (safe to run in a background worker)."""
    res = _stream_text(client, prompt)
    parsed = _extract_roster(res.get("raw")) or (res.get("parsed") if isinstance(res.get("parsed"), dict) else None)
    return (parsed or {}).get("roster") or []
