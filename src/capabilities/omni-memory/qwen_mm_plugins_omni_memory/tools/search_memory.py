from __future__ import annotations

from typing import Any

from pydantic import Field

from shared.content import json_text

from ..service import embed_client, load_store, memory_label, moment_brief
from . import MemoryRef


class SearchMemoryArgs(MemoryRef):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


TOOL = {"name": "search_memory", "args": SearchMemoryArgs}


def search_memory(
    video_path: str | None = None, namespace: str | None = None, query: str = "", top_k: int = 5
) -> dict[str, Any]:
    store = load_store(video_path, namespace)
    hits = store.search(query, client=embed_client(), k=max(1, min(int(top_k), 20)))
    return {
        "label": memory_label(video_path, namespace),
        "mode": hits.get("mode"),
        "people": [
            {"person_id": e.get("person_id"), "name": e.get("name"), "appearance": (e.get("appearance") or "")[:120]}
            for e in (hits.get("entities") or [])
        ],
        "facts": [
            {"key": t.get("key"), "statement": t.get("statement"), "confidence": t.get("confidence")}
            for t in (hits.get("semantic") or [])
        ],
        "moments": [moment_brief(store, o) for o in (hits.get("episodic") or [])],
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Hybrid search (dense embeddings + keyword, RRF-fused) across all three containers at once:
    people, semantic facts, and moments. Start here for an open question; narrow down with
    search_dialogue / search_facts / get_timeline when you know which kind of evidence you need.

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
        query: Descriptive statement of what you are looking for, not a question.
        top_k: Maximum number of matching results to return.
    """
    return [json_text(search_memory(**arguments))]
