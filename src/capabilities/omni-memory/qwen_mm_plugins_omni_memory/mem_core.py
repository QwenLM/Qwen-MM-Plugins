"""Reading a memory: the store plus hybrid retrieval over it.

MemoryStore is StoreBase (the containers, from omni_core) plus recall — dense embeddings and BM25,
RRF-fused, with entity-anchored multi-hop and phonetic name matching.

This is the READ half and it is all the MCP server has; building lives in skill/script/build_memory/
as a standalone program. Answering off the video itself is watch.py; the matching primitives are
text_match.py.
"""

import math
import os
import time

from .omni_core import (
    ANON_NAMES,
    NAME_STOP,
    RESIDENT_ENTITY_CAP,
    SALIENT_EMO,
    SALIENT_TONE,
    StoreBase,
    diag,
    embed_texts,
    env_int,
    get_embed_client,
    normalize_acoustic_events,
)
from .text_match import (
    BM25_B,
    BM25_BOOST,
    BM25_K1,
    cosine,
    kw,
    phonetic_name_match,
    query_person_names,
    rrf,
    tok_list,
)
from .watch import REPLAY_N

RESIDENT_KEY_CAP = 80  # max semantic keys kept resident

SCENE_RECALL_N = 4  # how many scene items to recall when the plan asks for SCENE_ENV

EP_RERANK = os.environ.get("MEM_EP_RERANK") == "1"  # rerank recalled clips against the original query

EP_TOPN = env_int("MEM_EP_TOPN", 6)  # clips kept after rerank


class MemoryStore(StoreBase):
    """StoreBase plus hybrid retrieval over it — what every query tool goes through."""

    def semantic_key_directory(self, limit=None):
        """Resident directory of semantic KEYS only. Under the buffer cap keep ALL (insertion
        order); over the cap keep the most frequently/recently used (freq×recency)."""
        cap = RESIDENT_KEY_CAP if limit is None else limit
        active = [t for t in self.semantic if t.get("key") and t.get("status") != "superseded"]
        if len(active) > cap:
            active = sorted(active, key=lambda t: (t.get("freq", 0), t.get("last_used", 0)), reverse=True)[:cap]
        return [t.get("key") for t in active]

    # ---------- resident (the cast of people + the directory of keys) ----------
    def _ledger_candidates(self):
        """person_id → the names it was most often heard called, for people with no resolved name.

        The ledger key splits into `key_parts`; `parts` is resident_context's output list and must not
        be rebound here.
        """
        cands: dict[str, list] = {}
        if not (ANON_NAMES and self.name_ledger):
            return cands
        for key, val in self.name_ledger.items():
            key_parts = key.split("|")
            if len(key_parts) != 4:
                continue
            nm, sid = key_parts[0], key_parts[1]
            if sid.startswith("P") and val.get("count", 0) >= 2:
                cands.setdefault(sid, []).append((nm, val["count"]))
        for sid in cands:
            cands[sid] = sorted(cands[sid], key=lambda x: -x[1])[:3]
        return cands

    def entity_directory(self, appearance_chars=120):
        """The resident cast of people, as data.

        Same content resident_context() renders as text, so a caller planning its own retrieval sees
        exactly what the pipeline's planner used to see — including `also_heard_as`, which for a
        person with no resolved name is the ONLY way to map a name someone says onto a person_id.
        """
        ents = self.entities or []
        if len(ents) > RESIDENT_ENTITY_CAP:
            ents = sorted(ents, key=lambda e: (e.get("freq", 0), e.get("last_used", 0)), reverse=True)[
                :RESIDENT_ENTITY_CAP
            ]
        cands = self._ledger_candidates()
        out = []
        for e in ents:
            pid = e.get("person_id")
            rec = {"person_id": pid, "name": e.get("name")}
            if e.get("appearance"):
                rec["appearance"] = (e.get("appearance") or "")[:appearance_chars]
            if not e.get("name") and cands.get(pid):
                rec["also_heard_as"] = [nm for nm, _ in cands[pid]]
            out.append(rec)
        return out

    # ---------- structured retrievers (deterministic, no model calls) ----------
    def _augment_phonetic(self, ents, sem, query):
        """③ Resolve person names in the question to entities, by exact name or by SOUND.

        Exact match first, then soundex / edit-similarity against entity names and against ledger
        candidate names — the latter covers anonymous entities whose real name was only ever heard,
        and mis-heard names ("Dara" for "Lara"). Each resolved person brings its triples along.
        """
        have = {e.get("person_id") for e in ents}
        seen_keys = {t.get("key") for t in sem}

        def _adopt(e, why):
            pid = e.get("person_id")
            if not pid or pid in have:
                return False
            ents.append(e)
            have.add(pid)
            for t in self.triples_of(pid):
                if t.get("key") not in seen_keys and t.get("status") != "superseded":
                    sem.append(t)
                    seen_keys.add(t.get("key"))
            diag(f"[MEM] > NAME-RESOLVED {why} ({pid})", flush=True)
            return True

        for qn in query_person_names(query):
            known = self.get_entity(qn)
            if known:
                # Naming a KNOWN person in the question used to resolve to nothing here, on the
                # assumption that whoever wrote the plan had already listed its person_id. Callers
                # now often pass just the question, so an exact name has to resolve too.
                _adopt(known, f"query '{qn}' = stored name")
                continue
            matched = False
            # Then: entity names that only SOUND like the queried one
            for e in self.entities:
                pid, nm = e.get("person_id"), e.get("name")
                if not nm or pid in have or nm.lower() in NAME_STOP:
                    continue
                if phonetic_name_match(qn, nm):
                    matched = _adopt(e, f"query '{qn}' ~ stored '{nm}'")
                    break
            if matched:
                continue
            # Last: ledger candidate names, which is where an anonymous entity's real name lives
            for key in self.name_ledger:
                parts = key.split("|")
                if len(parts) != 4:
                    continue
                ledger_nm, sid = parts[0], parts[1]
                if not sid.startswith("P") or sid in have or ledger_nm.lower() in NAME_STOP:
                    continue
                if self.name_ledger[key].get("count", 0) < 2:
                    continue
                if phonetic_name_match(qn, ledger_nm):
                    e = self.get_entity(sid)
                    if e and _adopt(e, f"query '{qn}' ~ ledger '{ledger_nm}'"):
                        matched = True
                        break
        return ents, sem

    def get_entity(self, name_or_id):
        return self._by_id.get(name_or_id) or self._by_name.get(str(name_or_id).lower())

    def triples_of(self, subject_id):
        return [
            t
            for t in self.semantic
            if t.get("subject_id") == subject_id or (t.get("subject") or "").lower() == str(subject_id).lower()
        ]

    def get_triples_by_keys(self, keys):
        """On-demand recall of triples by key (plan picks keys from the resident directory)."""
        kset = set(keys or [])
        return [t for t in self.semantic if t.get("key") in kset and t.get("status") != "superseded"]

    def recall_scene_env(self, query, k=SCENE_RECALL_N):
        """① on-demand scene recall: return the top-k scene_env items most relevant to the query
        (keyword overlap; fallback to first-k). Only called when PLAN picks the SCENE_ENV key."""
        if not self.scene_env:
            return []
        q = kw(query or "")
        ranked = sorted(self.scene_env, key=lambda s: -len(q & kw(s)))
        hit = [s for s in ranked if q & kw(s)]
        return (hit or list(self.scene_env))[:k]

    def episodic_in_time(self, start, end):
        return [o for o in self.episodic if o.get("win_end", 0) >= start and o.get("win_start", 1e18) <= end]

    def episodic_of_entity(self, person_id):
        out = []
        for o in self.episodic:
            p = o.get("parsed") or {}
            vids = [e.get("person_id") for e in (p.get("visual", {}) or {}).get("key_entities", []) or []]
            sids = [u.get("speaker_id") for u in (p.get("audio", {}) or {}).get("utterances", []) or []]
            if person_id in vids or person_id in sids:
                out.append(o)
        return out

    @staticmethod
    def _ep_entity_text(rec):
        p = rec.get("parsed") or {}
        vis = p.get("visual", {}) or {}
        names = []
        for e in vis.get("key_entities", []) or []:
            names.append(" ".join(filter(None, [e.get("name"), e.get("ref")])))
        return " ".join(names)

    def _dense_rank(self, items, qvec):
        # `is not None` for the same reason as build_index: these are ndarrays now.
        scored = [(i, cosine(qvec, it["emb"])) for i, it in enumerate(items) if it.get("emb") is not None]
        return [i for i, _ in sorted(scored, key=lambda x: -x[1])]

    def _sparse_rank(self, items, query, textfn, boostfn=None):
        """BM25 over `items`, returning their indices best-first; items matching nothing are omitted.

        Rare terms carry more weight (idf), repeats saturate rather than accumulate (k1), and length is
        normalised against the collection average (b) — without which long clip transcripts drift to
        the top merely for holding more distinct words.

        `boostfn` names a second, much shorter field (the clip's entity names), scored as its own BM25
        against its own average length and added with weight BM25_BOOST rather than folded into the
        main text, so a name hit is not diluted by the transcript's length.

        Statistics come from the collection actually passed in, so the same record scores differently
        depending on what it is ranked against — correct, since idf is a property of the collection.
        """
        qterms = kw(query)
        if not qterms or not items:
            return []

        def field(fn):
            """(per-doc term-frequency, per-doc length, doc-frequency of each query term, avg length)"""
            tfs, lens, df = [], [], {}
            for it in items:
                toks = tok_list(fn(it))
                tf = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                tfs.append(tf)
                lens.append(len(toks))
                for t in qterms & tf.keys():
                    df[t] = df.get(t, 0) + 1
            n = len(items)
            return tfs, lens, df, (sum(lens) / n if n else 0.0) or 1.0

        def bm25(tfs, lens, df, avgdl, weight):
            n = len(items)
            # Robertson/Sparck-Jones idf with the +1 guard, so a term in every document scores ~0
            # rather than going negative.
            idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
            out = [0.0] * n
            for i, (tf, dl) in enumerate(zip(tfs, lens)):
                s = 0.0
                for t, w in idf.items():
                    f = tf.get(t, 0)
                    if f:
                        s += w * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
                if s:
                    out[i] = s * weight
            return out

        scores = bm25(*field(textfn), 1.0)
        if boostfn:
            for i, s in enumerate(bm25(*field(boostfn), BM25_BOOST)):
                scores[i] += s
        ranked = [(i, s) for i, s in enumerate(scores) if s > 0]
        return [i for i, _ in sorted(ranked, key=lambda x: -x[1])]

    # ---------- hybrid search + entity-anchored multi-hop ----------
    def search(self, query, client=None, k=5):
        """Returns {'entities','semantic','episodic','mode'}. Hybrid RRF per pool, plus:
        if the query names a known entity, enrich with its triples + episodic mentions."""
        qvec = None
        if self.dense_ok:
            v = embed_texts(client or get_embed_client(), [query])
            qvec = v[0] if v else None
        mode = "hybrid" if qvec is not None else "keyword"

        def fuse(items, textfn, boostfn=None):
            if not items:
                return []
            lists = []
            sp = self._sparse_rank(items, query, textfn, boostfn)
            if sp:
                lists.append(sp)
            if qvec is not None:
                lists.append(self._dense_rank(items, qvec))
            if not lists:
                return []
            fused = rrf(lists)
            order = sorted(fused, key=lambda i: -fused[i])
            return [items[i] for i in order[:k]]

        ep = fuse(self.episodic, self._ep_text, boostfn=self._ep_entity_text)
        sem = fuse(self.semantic, lambda f: f.get("statement", ""))

        # entity-anchored multi-hop (cross-modal): matched entity → its triples + episodic mentions
        qk = kw(query)
        matched = [e for e in self.entities if qk & (kw(e.get("name")) | kw(e.get("appearance")) | kw(e.get("ref")))]
        for e in matched:
            pid = e.get("person_id")
            for t in self.triples_of(pid):
                if t not in sem:
                    sem.append(t)
            for o in self.episodic_of_entity(pid)[:k]:
                if o not in ep:
                    ep.append(o)

        if not ep and not sem and not matched:  # nothing matched — fall back to a broad recall
            ep, sem = self.episodic[:k], self.semantic[:k]
        diag(
            f"[MEM] ▸ SEARCH q={query!r} mode={mode} -> entities={len(matched)} semantic={len(sem)} episodic={len(ep)}",
            flush=True,
        )
        return {"entities": matched, "semantic": sem, "episodic": ep, "mode": mode}

    def search_episodic(self, query, client=None, k=5):
        """Episodic-only hybrid recall (dense+keyword RRF); keyword-only if no client/index."""
        if not self.episodic:
            return []
        qvec = None
        if self.dense_ok:
            v = embed_texts(client or get_embed_client(), [query])
            qvec = v[0] if v else None
        lists = []
        sp = self._sparse_rank(self.episodic, query, self._ep_text, boostfn=self._ep_entity_text)
        if sp:
            lists.append(sp)
        if qvec is not None:
            lists.append(self._dense_rank(self.episodic, qvec))
        if not lists:
            return []
        fused = rrf(lists)
        order = sorted(fused, key=lambda i: -fused[i])
        return [self.episodic[i] for i in order[:k]]

    def search_semantic(self, query, client=None, k=5):
        """Semantic-only hybrid recall (dense+keyword RRF) — the counterpart to search_episodic.

        Used when a plan names no fact keys: the caller has a question but does not know which keys
        exist, so match on statement content instead of by exact key.
        """
        active = [t for t in (self.semantic or []) if t.get("status") != "superseded"]
        if not active:
            return []
        qvec = None
        if self.dense_ok:
            v = embed_texts(client or get_embed_client(), [query])
            qvec = v[0] if v else None
        lists = []
        sp = self._sparse_rank(active, query, lambda t: t.get("statement", "") or "")
        if sp:
            lists.append(sp)
        if qvec is not None:
            lists.append(self._dense_rank(active, qvec))
        if not lists:
            return []
        fused = rrf(lists)
        order = sorted(fused, key=lambda i: -fused[i])
        return [active[i] for i in order[:k]]

    def rerank_episodic(self, cands, query, k=EP_TOPN, client=None):
        """S2: re-score accumulated candidates against the ORIGINAL question (not the decomposed
        episode_queries) via dense+keyword RRF, keep top-k. Restores cross-subquery comparability
        and trims episodic noise. Returns re-ranked (relevance order)."""
        if not cands or len(cands) <= k:
            return cands
        lists = []
        sp = self._sparse_rank(cands, query, self._ep_text, boostfn=self._ep_entity_text)
        if sp:
            lists.append(sp)
        if self.dense_ok:
            v = embed_texts(client or get_embed_client(), [query])
            if v:
                lists.append(self._dense_rank(cands, v[0]))
        if not lists:
            return cands[:k]
        fused = rrf(lists)
        order = sorted(fused, key=lambda i: -fused[i])
        return [cands[i] for i in order[:k]]

    def run_plan(self, plan, query, client=None, k=5):
        """Execute a retrieval plan -> hits. Consumes entities + keys + episode_queries + time_ranges."""
        _keys = list(plan.get("need_keys") or [])
        want_scene = "SCENE_ENV" in _keys or bool(plan.get("need_scene"))  # ① on-demand scene key
        _keys = [x for x in _keys if x != "SCENE_ENV"]  # not a semantic triple key
        ents = [e for e in (self.get_entity(x) for x in (plan.get("need_entities") or [])) if e]
        # Exact lookup by design: whoever wrote the plan was shown the complete key directory
        # (entity_directory + semantic_key_directory, the same thing resident_context renders), so
        # guessing keys here would only add facts it chose not to ask for. Matching facts by content
        # is search_semantic, which search_facts exposes as its own tool.
        sem = self.get_triples_by_keys(_keys)
        if os.environ.get("MEM_PHONETIC_FALLBACK", "1") != "0":  # ③ recover mis-heard names by sound
            ents, sem = self._augment_phonetic(ents, sem, query)
        _t = time.time()  # P4: frequency/recency instrumentation
        for it in ents + sem:
            it["freq"] = it.get("freq", 0) + 1
            it["last_used"] = _t
        queries = list(plan.get("episode_queries") or [])
        if not queries and not sem and not ents and not plan.get("time_ranges"):
            queries = [query]
        if query not in queries:
            queries.append(query)
        scene = self.recall_scene_env(" ".join(queries) or query) if want_scene else []
        ep, seen = [], set()
        for q in queries:
            for o in self.search_episodic(q, client=client, k=k):
                if o.get("idx") not in seen:
                    seen.add(o.get("idx"))
                    ep.append(o)
        for tr in plan.get("time_ranges") or []:  # P2: time-range recall
            try:
                s, e = float(tr[0]), float(tr[1])
            except Exception:
                continue
            for o in self.episodic_in_time(s, e):
                if o.get("idx") not in seen:
                    seen.add(o.get("idx"))
                    ep.append(o)
        if EP_RERANK:  # S2: rerank vs original query + trim noise
            ep = self.rerank_episodic(ep, query, k=EP_TOPN, client=client)
        ranked = list(ep)  # relevance/recall order (for replay pick)
        ep.sort(key=lambda o: o.get("win_start", 0))  # chronological order (for answer evidence)
        replays = self._resolve_replays(plan, ranked, sem, n=REPLAY_N)  # multi-anchor candidates (deferred)
        diag(
            f"[MEM] ▸ RUN_PLAN entities={len(ents)} semantic={len(sem)} episodic={len(ep)} "
            f"scene={len(scene)} replays={[c.get('idx') for c in replays]}",
            flush=True,
        )
        return {
            "entities": ents,
            "semantic": sem,
            "episodic": ep,
            "scene": scene,
            "replay": (replays[0] if replays else None),
            "replays": replays,
            "mode": "plan",
        }

    def _resolve_replay(self, plan, ep):
        """Pick EXACTLY ONE clip to re-watch (strict). Returns a clip dict or None."""
        tgt = plan.get("replay_target")
        tgt = tgt if isinstance(tgt, dict) else {}
        by, val = tgt.get("by"), tgt.get("value")
        idx = None
        if by == "clip_idx":
            try:
                idx = int(val)
            except Exception:
                idx = None
        elif by == "time":
            try:
                t = float(val[0]) if isinstance(val, (list, tuple)) else float(val)
            except Exception:
                t = None
            if t is not None:
                for c in self.clips:
                    if c.get("win_start", 0) <= t <= c.get("win_end", 0):
                        idx = c.get("idx")
                        break
        if idx is None and plan.get("time_ranges"):
            try:
                s = float(plan["time_ranges"][0][0])
                for c in self.clips:
                    if c.get("win_start", 0) <= s <= c.get("win_end", 1e18):
                        idx = c.get("idx")
                        break
            except Exception:
                pass
        if idx is None and ep:
            idx = ep[0].get("idx")
        if idx is None and self.clips:
            idx = self.clips[0].get("idx")
        for c in self.clips:
            if c.get("idx") == idx:
                return c
        return None

    def _clip_playable(self, c):
        return bool(c and (c.get("oss_key") or c.get("path")))

    def _resolve_replays(self, plan, ep, sem_hits=None, n=REPLAY_N):
        """Pick which clips to re-watch: dense episodic ranking first, time hints only as tiebreaker.

        Ordering by time or semantic anchors ahead of the dense ranking was measured to surface worse
        clips, so it is deliberately not done.
        """
        order = []
        for o in ep:  # dense relevance order (primary signal)
            order.append(o.get("idx"))
        primary = self._resolve_replay(plan, ep)  # time-anchor as secondary
        if primary and primary.get("idx") not in set(o.get("idx") for o in ep[:3]):
            order.insert(0, primary.get("idx"))  # only boost if NOT already in top-3
        by_idx = {c.get("idx"): c for c in self.clips}
        _MIN_GAP = 30  # minimum seconds between selected replay clips
        out, seen = [], set()
        selected_times = []  # (win_start, win_end) of selected clips
        for idx in order:
            if idx in seen:
                continue
            seen.add(idx)
            c = by_idx.get(idx)
            if not self._clip_playable(c):
                continue
            # temporal diversity: skip if too close to an already-selected clip
            cs, ce = c.get("win_start", 0), c.get("win_end", 0)
            if n >= 3 and len(out) >= 1:
                too_close = any(abs(cs - ss) < _MIN_GAP and abs(ce - se) < _MIN_GAP for ss, se in selected_times)
                if too_close:
                    continue
            out.append(c)
            selected_times.append((cs, ce))
            if len(out) >= max(1, n):
                break
        return out

    # ---------- evidence assembly (pyramid: Core → Semantic → Episode) ----------
    def name_of(self, pid):
        """L3 query-time mapping: person_id → current canonical name (or None)."""
        e = self._by_id.get(pid)
        return e.get("name") if e else None

    def _ep_evidence_line(self, o):
        p = o.get("parsed") or {}
        vis = p.get("visual", {}) or {}
        aud = p.get("audio", {}) or {}
        head = f"- [{o.get('win_start')}-{o.get('win_end')}s] {vis.get('visual_caption', '')}"
        lines = [head]
        for u in aud.get("utterances") or []:
            sid = u.get("speaker_id") or "unknown"
            who = self.name_of(sid) or sid  # L3: show live name, not frozen speaker_id
            pl = u.get("paralinguistic") or {}
            tags = [pl.get("emotion")] if (pl.get("emotion") or "") not in SALIENT_EMO else []
            if (pl.get("tone") or "") not in SALIENT_TONE:
                tags.append(pl["tone"])
            tg = f"[{','.join(t for t in tags if t)}]" if tags else ""
            ts = f"[{u.get('start_sec')}-{u.get('end_sec')}s]" if u.get("start_sec") is not None else ""
            lines.append(f"    🗣{ts}[{who}]{tg} {u.get('text', '')}")
        ac = aud.get("acoustic", {}) or {}
        if ac.get("events"):
            evts = normalize_acoustic_events(ac["events"])
            parts = []
            for e in evts:
                label = e.get("event", str(e)) if isinstance(e, dict) else str(e)
                ts = ""
                if isinstance(e, dict) and e.get("start_sec") is not None:
                    ts = f"[{e['start_sec']}-{e.get('end_sec', '?')}s]"
                    cont = e.get("continues_from_previous")
                    if cont:
                        ts += "(cont)"
                parts.append(f"{ts}{label}" if ts else label)
            lines.append(f"    🔊 {'; '.join(parts)}")
        ac_scene = ac.get("scene", "")
        if ac_scene and ac_scene not in ("unknown", ""):
            lines.append(f"    🔊env: {ac_scene}")
        return "\n".join(lines)

    def evidence_text(self, hits):
        out = []
        if hits.get("entities"):
            out.append("## Entities (Core)")
            for e in hits["entities"]:
                nm = e.get("name") or e.get("ref") or e.get("person_id")
                out.append(
                    f"- {e.get('person_id')} = {nm}: {e.get('appearance', '')}"
                    + (f"; last: {e.get('last_action', '')}" if e.get("last_action") else "")
                )
        if hits.get("scene"):  # ① on-demand environment (recalled only when PLAN picked SCENE_ENV)
            out.append("## Environment / layout (Scene)")
            for s in hits["scene"]:
                out.append(f"- {s}")
        if hits.get("semantic"):
            out.append("## Stable facts (Semantic)")
            for f in hits["semantic"]:
                out.append(f"- {f.get('statement', '')}  (conf={f.get('confidence', '')})")
        if hits.get("episodic"):
            out.append("## Relevant moments (Episode)")
            for o in hits["episodic"]:
                out.append(self._ep_evidence_line(o))
        return "\n".join(out) if out else "(no relevant memory found)"
