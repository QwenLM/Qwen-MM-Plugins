"""The store as the build WRITES it: the setters, the vector codec on the way out, and the index build.

Retrieval is not here. It lives on the query side, in the server package's mem_core.MemoryStore. Both
classes carry the name MemoryStore and both extend omni_core.StoreBase, and StoreBase is what makes a
store written by this one readable by that one: the fields, and the from_dict that reads them back,
are defined once for both.

Nothing on this side ranks, searches or answers — a build only ever appends and re-indexes.
"""

import numpy as np
from omni_core import EMB_DTYPE, StoreBase, emb_encode, embed_texts, get_embed_client


def _emb_out(d):
    """Shallow copy of a record with its vector encoded for storage.

    Always copies, even when there is no vector: snapshot_for_async relies on the per-dict copy to
    isolate the top-level in-place mutations that happen while the snapshot is being serialized off
    the main thread."""
    if d.get("emb") is None:
        return dict(d)
    return {**d, "emb": emb_encode(d["emb"])}


class MemoryStore(StoreBase):
    """StoreBase plus everything a build needs to fill it in and index it."""

    def add_episodic(self, rec):
        self.episodic.append(rec)

    def set_semantic(self, triples):
        self.semantic = [t for t in (triples or []) if t.get("status") != "superseded"]

    def set_clips(self, clips):
        self.clips = [
            {
                "idx": c["idx"],
                "path": c.get("path"),
                "win_start": c["win_start"],
                "win_end": c["win_end"],
                "oss_key": c.get("oss_key"),
            }
            for c in (clips or [])
        ]

    def add_clips(self, clips):
        """Append clip pointers (incremental multi-upload); keeps existing ones."""
        self.clips = list(self.clips) + [
            {
                "idx": c["idx"],
                "path": c.get("path"),
                "win_start": c["win_start"],
                "win_end": c["win_end"],
                "oss_key": c.get("oss_key"),
            }
            for c in (clips or [])
        ]

    def clip_idx_next(self):
        """Next global clip index (0 if empty)."""
        return max((c.get("idx", -1) for c in self.clips), default=-1) + 1

    def time_offset(self):
        """Global time offset for the next segment = video seconds already ingested."""
        if self.processed_sec:
            return float(self.processed_sec)
        return float(max((e.get("win_end", 0) or 0 for e in self.episodic), default=0.0))

    def clear(self):
        self.__init__()

    def snapshot_for_async(self):
        """Cheap, race-safe snapshot for BACKGROUND serialization (per-clip incremental save).
        Only shallow per-dict copies: isolates top-level in-place mutations that happen after
        this returns — driver update_state (entity fields) and build_index (adds 'emb'). Nested
        parsed/raw/state_after are shared (never mutated after a clip is created). The heavy
        json.dumps is done off-thread by the caller, so the main loop pays only these copies.

        _emb_out does the per-dict copy for the two containers that hold vectors, encoding them on the
        way out — the caller's json.dumps cannot serialize an ndarray, so this is also what keeps a
        snapshot serializable at all."""
        return {
            "version": 3,
            "global_summary": self.global_summary,
            "global_nodes": [dict(n) for n in self.global_nodes],
            "scene_env": list(self.scene_env),
            "entities": [dict(e) for e in self.entities],
            "episodic": [_emb_out(r) for r in self.episodic],
            "semantic": [_emb_out(t) for t in self.semantic],
            "clips": [dict(c) for c in self.clips],
            "driver_state": self.driver_state,
            "processed_sec": self.processed_sec,
            "dense_ok": self.dense_ok,
            "name_ledger": dict(self.name_ledger),
        }

    # ---------- dense index ----------
    def build_index(self, client):
        """Embed for dense retrieval. Episodic is INCREMENTAL (only clips missing 'emb' —
        cheap for multi-upload); semantic is re-embedded fully (statements change via CRUD)."""
        ec = get_embed_client()  # embeddings ALWAYS DashScope, regardless of the chat client
        # `is None`, not falsiness: a vector is an ndarray in memory, and bool() on one with more than
        # a single element raises rather than answering.
        ep_need = [e for e in self.episodic if e.get("emb") is None]
        ep_vecs = embed_texts(ec, [self._ep_text(e) for e in ep_need]) if ep_need else []
        sem_vecs = embed_texts(ec, [f.get("statement", "") for f in self.semantic]) if self.semantic else []
        if ep_vecs is None or sem_vecs is None:
            self.dense_ok = False
            return False
        for e, v in zip(ep_need, ep_vecs):
            e["emb"] = np.asarray(v, dtype=EMB_DTYPE)
        for f, v in zip(self.semantic, sem_vecs):
            f["emb"] = np.asarray(v, dtype=EMB_DTYPE)
        self.dense_ok = True
        return True
