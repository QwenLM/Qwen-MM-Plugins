from __future__ import annotations

from typing import Any

from shared.content import json_text

from ..service import load_store, memory_label, utterances
from . import MemoryRef


class GetPeopleArgs(MemoryRef):
    person_id: str | None = None


TOOL = {"name": "get_people", "args": GetPeopleArgs}


def get_people(
    video_path: str | None = None, namespace: str | None = None, person_id: str | None = None
) -> dict[str, Any]:
    store = load_store(video_path, namespace)
    ents = store.entities or []
    if person_id:
        e = store.get_entity(person_id)
        if not e:
            return {"error": f"no such person: {person_id}", "known": [x.get("person_id") for x in ents]}
        ents = [e]
    people = []
    for e in ents:
        pid = e.get("person_id")
        eps = store.episodic_of_entity(pid) if pid else []
        utt = utterances(store, person_id=pid)
        people.append(
            {
                "person_id": pid,
                "name": e.get("name"),
                "appearance": e.get("appearance") or "",
                "last_action": e.get("last_action") or "",
                "clips_present": len(eps),
                "first_seen_sec": round(min((o.get("win_start", 0) for o in eps), default=0), 1),
                "last_seen_sec": round(max((o.get("win_end", 0) for o in eps), default=0), 1),
                "utterance_count": len(utt),
                "facts": [{"key": t.get("key"), "statement": t.get("statement")} for t in (store.triples_of(pid) or [])]
                if pid
                else [],
            }
        )
    return {"label": memory_label(video_path, namespace), "count": len(people), "people": people}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Person dossiers: canonical person_id, resolved name, appearance, when they are on screen, how
    much they speak, and the semantic facts attached to them. Identities are carried across
    overlapping clips, so the same person keeps one person_id for the whole video.

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
        person_id: Canonical id such as P001. Omit to get every person in the video.
    """
    return [json_text(get_people(**arguments))]
