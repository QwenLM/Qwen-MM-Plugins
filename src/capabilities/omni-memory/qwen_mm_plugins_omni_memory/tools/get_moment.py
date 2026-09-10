from __future__ import annotations

import os
from typing import Any

from shared.content import json_text

from ..service import load_store, memory_label
from . import MemoryRef


class GetMomentArgs(MemoryRef):
    idxs: list[int]


TOOL = {"name": "get_moment", "args": GetMomentArgs}


def get_moment(
    video_path: str | None = None, namespace: str | None = None, idxs: list[int] | None = None
) -> dict[str, Any]:
    """Full detail for one or more clips — the most information-dense read.

    Also returns each clip's path on disk. The memory is one record per 30s window, so a caller that
    needs finer detail has somewhere to go: replay_and_answer for anything in the audio, or whatever
    frame-level tooling it happens to have for the picture.
    """
    store = load_store(video_path, namespace)
    by_idx = {o.get("idx"): o for o in (store.episodic or [])}
    clip_by_idx = {c.get("idx"): c for c in (store.clips or [])}
    out, missing, invalid = [], [], []
    for i in idxs or []:
        try:
            ix = int(i)
        except (TypeError, ValueError):
            invalid.append(i)
            continue
        rec = by_idx.get(ix)
        if rec is None:
            missing.append(ix)
            continue
        vis = (rec.get("parsed") or {}).get("visual") or {}
        aud = (rec.get("parsed") or {}).get("audio") or {}
        clip = clip_by_idx.get(ix) or {}
        path = clip.get("oss_key") or clip.get("path")
        out.append(
            {
                "idx": rec.get("idx"),
                "win_start": rec.get("win_start"),
                "win_end": rec.get("win_end"),
                "visual_caption": vis.get("visual_caption") or "",
                "scene_continuity": vis.get("scene_continuity"),
                "scene_env_update": vis.get("scene_env_update") or [],
                "people": [
                    {
                        "person_id": e.get("person_id"),
                        "name": store.name_of(e.get("person_id")),
                        "appearance": (e.get("appearance") or "")[:120],
                        "action": e.get("action") or e.get("last_action") or "",
                    }
                    for e in (vis.get("key_entities") or [])
                ],
                "utterances": [
                    {
                        "speaker_id": u.get("speaker_id"),
                        "speaker_name": store.name_of(u.get("speaker_id")) if u.get("speaker_id") else None,
                        "text": u.get("text") or "",
                        "paralinguistic": u.get("paralinguistic") or None,
                    }
                    for u in (aud.get("utterances") or [])
                ],
                "acoustic_events": ((aud.get("acoustic") or {}).get("events")) or [],
                "clip_path": path if (path and os.path.exists(path)) else None,
            }
        )
    res = {"label": memory_label(video_path, namespace), "count": len(out), "moments": out}
    if (missing or invalid) and by_idx:
        # Only when there IS a range. An empty episodic container used to report [0, 0] here, which
        # reads as "index 0 exists" — the opposite of true, and aimed at exactly the caller who has
        # just been told their index was not found. With the key absent, missing_idxs against count 0
        # says the only thing that is actually known.
        res["available_range"] = [min(by_idx), max(by_idx)]
    if missing:
        res["missing_idxs"] = missing
    if invalid:
        res["invalid_idxs"] = invalid
    return res


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Full detail for one or more clips — the most information-dense read. Returns the visual caption,
    every spoken line with its speaker, who is present and what they are doing, acoustic events,
    scene changes, and the path to the 30s clip file itself. Reach for this when a moment brief from
    plan_and_search or get_timeline is too coarse.

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
        idxs: Clip indices to expand, from search_* or get_timeline.
    """
    return [json_text(get_moment(**arguments))]
