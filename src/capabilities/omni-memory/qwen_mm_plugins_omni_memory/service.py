"""Read-only service boundary over the memory core — what MORE THAN ONE tool needs.

Locating a memory, loading it, and the few helpers several tools share. A helper only one tool uses
lives in that tool's own module instead, so this file stays the answer to "what do the tools have in
common" rather than a drawer for everything that is not a schema.

Building is NOT here — it is a long, serial, stateful job and lives in skill/script/build_memory/,
driven by the agent through Bash. This module only reads memories that already exist, so the MCP
server stays stateless and cacheable.

Memory location, either form:
  · video_path  → <video_path>.memory/store.json        (per-video, preferred; matches video-memory)
  · namespace   → $MEM_LOCAL_DIR/<namespace>/store.json when configured, otherwise
                  <video-dir>/<namespace>/store.json (several videos may stream into one memory)
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from . import config, mem_core, omni_core

# Uncatalogued MEM_* knobs the environment set; logged by on_start because they change retrieval or
# determinism without surfacing anywhere else.
ENV_TUNING = {k: v for k, v in sorted(os.environ.items()) if k.startswith("MEM_")}

MEMORY_SUFFIX = ".memory"
DEFAULT_OMNI_MODEL = omni_core.MODEL  # what an unspecified `model` resolves to, for tool descriptions

_stores: dict[str, tuple[float, Any]] = {}  # path → (mtime, MemoryStore)
_lock = threading.Lock()
_tl = threading.local()


# ─────────────────────────────────────────────────────────── locating a memory


def memory_root() -> str:
    """Configured shared root used when callers pass `namespace` without `video_path`.

    config.local_dir() already resolves MEM_LOCAL_DIR — reading it here as well would bypass the
    settings-file fallback that lookup goes through.
    """
    return config.local_dir()


def memory_dir(video_path: str | None = None, namespace: str | None = None) -> Path:
    if namespace:
        ns = str(namespace).strip().strip("/")
        if not ns or ns in {".", ".."} or "/" in ns or "\\" in ns:
            raise ValueError("namespace must be a simple non-empty name")
        root = memory_root()
        if root:
            return Path(root) / ns
        if video_path:
            return Path(video_path).expanduser().resolve().parent / ns
        raise ValueError("namespace requires video_path when MEM_LOCAL_DIR is not configured")
    if video_path:
        return Path(str(Path(video_path).expanduser().resolve()) + MEMORY_SUFFIX)
    raise ValueError("pass either video_path or namespace")


def memory_label(video_path: str | None, namespace: str | None) -> str:
    return namespace or (Path(video_path).name if video_path else "?")


def library_namespaces() -> list[str]:
    """Namespaces under the shared library root. Only used to make a not-found status actionable —
    per-video memories are located by video_path, so there is no listing tool."""
    root = Path(memory_root())
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if (d / "store.json").is_file())


# ─────────────────────────────────────────────────────────── loading


def load_store(video_path: str | None = None, namespace: str | None = None):
    """Load (and cache) a MemoryStore. Cache is invalidated by store.json mtime."""
    mdir = memory_dir(video_path, namespace)
    sp = mdir / "store.json"
    if not sp.is_file():
        raise FileNotFoundError(
            f"no memory at {mdir} — build it first: python3 script/build_memory/build_memory.py <video>"
        )
    mt = sp.stat().st_mtime
    key = str(sp)
    with _lock:
        hit = _stores.get(key)
        if hit and hit[0] == mt:
            return hit[1]
    store = mem_core.MemoryStore.from_dict(json.loads(sp.read_text(encoding="utf-8")))
    with _lock:
        _stores[key] = (mt, store)
    return store


def preload() -> None:
    """Warm the cache for a fixed library root (called from on_start in a daemon thread)."""
    for ns in library_namespaces()[:8]:
        try:
            load_store(namespace=ns)
        except Exception:
            pass


def embed_client():
    """Client used ONLY to embed the query for dense retrieval. None if no key is configured."""
    c = getattr(_tl, "embed", None)
    if c is None:
        try:
            # `or False` so an absent credential is remembered too, like the exception below.
            c = _tl.embed = omni_core.get_embed_client() or False
        except Exception:
            c = _tl.embed = False
    return c or None


# ───────────────────────────────── dialogue: read by get_people and get_person_dialogue


def clip_utterances(store, rec: dict) -> list[dict]:
    """Expand ONE clip's utterances: absolute time + resolved speaker name.

    Per-clip `start_sec` may be either absolute or relative to the window, so it is normalised
    against the window start. Speaker attribution is the omni model's own (prompt Part B6: each line
    is bound to a canonical person_id using lip movement + who is visibly speaking) — a pure read,
    no ASR involved.
    """
    aud = (rec.get("parsed") or {}).get("audio") or {}
    base = rec.get("win_start", 0) or 0
    out = []
    for u in aud.get("utterances") or []:
        sid = u.get("speaker_id")
        t0 = u.get("start_sec")
        t0 = base if t0 is None else (t0 if t0 >= base else base + t0)
        out.append(
            {
                "clip_idx": rec.get("idx"),
                "start_sec": round(t0, 1),
                "speaker_id": sid,
                "speaker_name": store.name_of(sid) if sid else None,
                "text": u.get("text") or "",
                "paralinguistic": u.get("paralinguistic") or None,
            }
        )
    return out


def utterances(
    store, person_id: str | None = None, start_sec: float | None = None, end_sec: float | None = None
) -> list[dict]:
    """Every utterance in the video, optionally filtered by speaker and/or time window."""
    out = []
    for rec in store.episodic or []:
        for u in clip_utterances(store, rec):
            if person_id and u["speaker_id"] != person_id:
                continue
            if start_sec is not None and u["start_sec"] < start_sec:
                continue
            if end_sec is not None and u["start_sec"] > end_sec:
                continue
            out.append(u)
    out.sort(key=lambda x: (x["start_sec"], x["clip_idx"] or 0))
    return out


# ──────────── one clip in brief: read by get_timeline, search_memory and plan_and_search


def moment_brief(store, rec: dict) -> dict[str, Any]:
    vis = (rec.get("parsed") or {}).get("visual") or {}
    aud = (rec.get("parsed") or {}).get("audio") or {}
    utt = aud.get("utterances") or []
    return {
        "idx": rec.get("idx"),
        "win_start": rec.get("win_start"),
        "win_end": rec.get("win_end"),
        "visual_caption": (vis.get("visual_caption") or "")[:400],
        "people": [e.get("person_id") for e in (vis.get("key_entities") or [])],
        "utterance_count": len(utt),
        "first_line": (utt[0].get("text") if utt else None),
    }
