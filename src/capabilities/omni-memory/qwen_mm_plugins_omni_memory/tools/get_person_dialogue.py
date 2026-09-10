from __future__ import annotations

from typing import Any

from pydantic import Field

from shared.content import json_text

from ..service import load_store, memory_label, utterances
from . import MemoryRef


class GetPersonDialogueArgs(MemoryRef):
    person_id: str = ""
    start_sec: float | None = None
    end_sec: float | None = None
    limit: int = Field(default=200, ge=1, le=1000)


TOOL = {"name": "get_person_dialogue", "args": GetPersonDialogueArgs}


def get_person_dialogue(
    video_path: str | None = None,
    namespace: str | None = None,
    person_id: str = "",
    start_sec: float | None = None,
    end_sec: float | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    store = load_store(video_path, namespace)
    if person_id and not store.get_entity(person_id):
        return {"error": f"no such person: {person_id}", "known": [e.get("person_id") for e in (store.entities or [])]}
    utt = utterances(store, person_id=person_id or None, start_sec=start_sec, end_sec=end_sec)
    return {
        "label": memory_label(video_path, namespace),
        "person_id": person_id or "(all)",
        "speaker_name": store.name_of(person_id) if person_id else None,
        "total": len(utt),
        "returned": min(len(utt), max(1, limit)),
        "utterances": utt[: max(1, limit)],
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Everything one person said, in time order, with timestamps and tone. Use this for 'what did X
    say', 'did X mention Y', 'who said Z'. Each line was bound to its speaker by the omni model from
    lip movement and who is visibly speaking, so attribution survives overlapping speech and similar
    voices.

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
        person_id: Canonical id such as P001 (see get_memory_overview / get_people). Empty means
            every speaker, in time order.
        start_sec: Optional time window start.
        end_sec: Optional time window end.
        limit: Maximum number of dialogue entries to return.
    """
    return [json_text(get_person_dialogue(**arguments))]
