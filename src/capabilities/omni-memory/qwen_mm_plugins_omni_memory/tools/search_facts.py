from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from shared.content import json_text

from ..service import embed_client, load_store, memory_label
from . import MemoryRef


class SearchFactsArgs(MemoryRef):
    query: str | None = None
    key_prefix: str | None = None
    subject_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=20)


TOOL = {"name": "search_facts", "args": SearchFactsArgs}


def _facts_by_prefix(store, key_prefix: str) -> tuple[list, str]:
    """Facts whose key starts with `key_prefix`, trying the person_id and the name form of it.

    A key is minted at induction time from whatever the subject was called *then*: the resolved name
    ("Matthew/role") once one is known, the person_id ("P001/role") while it is not. Name alignment
    runs afterwards and does not rewrite keys, so one person can end up with keys under both — and a
    caller has no way to tell which. Mapping one form to the other, as this used to do, then returns
    nothing at all: bedroom_01 keeps all 27 of P001's facts under `P001/` and reports Lily as the
    name, so both `P001/` and `Lily/` came back empty. Look under every form of the prefix instead.
    """
    pref = (key_prefix or "").strip()
    if not pref:
        return [], "key_prefix="
    head = pref.rstrip("/").split("/")[0]
    forms = [pref]
    # Both directions: a person_id also answers under its name, and a name under its person_id.
    other = store.name_of(head) if re.fullmatch(r"P\d+", head) else (store.get_entity(head) or {}).get("person_id")
    if other:
        forms.append(pref.replace(head, str(other), 1))
    seen, out = set(), []
    for f in forms:
        for t in store.semantic or []:
            k = str(t.get("key", ""))
            if k.startswith(f) and k not in seen:
                seen.add(k)
                out.append(t)
    return out, "key_prefix=" + "|".join(dict.fromkeys(forms))


def search_facts(
    video_path: str | None = None,
    namespace: str | None = None,
    query: str | None = None,
    key_prefix: str | None = None,
    subject_id: str | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Three entry points into the semantic container: by subject, by key prefix, or by search."""
    store = load_store(video_path, namespace)
    if subject_id:
        triples = store.triples_of(subject_id) or []
        how = f"subject_id={subject_id}"
    elif key_prefix:
        triples, how = _facts_by_prefix(store, key_prefix)
    elif query:
        # search_semantic rather than the full store.search(): the latter fuses episodic as well and
        # the result is thrown away here.
        triples = store.search_semantic(query, client=embed_client(), k=max(1, min(int(top_k), 20)))
        how = f"query={query!r}"
    else:
        return {
            "error": "pass one of: query, key_prefix, subject_id",
            "key_directory": store.semantic_key_directory() or [],
        }
    return {
        "label": memory_label(video_path, namespace),
        "how": how,
        "count": len(triples),
        "facts": [
            {
                "key": t.get("key"),
                "statement": t.get("statement"),
                "confidence": t.get("confidence"),
                "evidence_sec": t.get("t0"),
            }
            for t in triples
        ],
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Stable semantic facts, induced across clips and kept de-duplicated (keys look like
    'P001/identity/name' or 'event:room_tidying/plan'). Three ways in: query for semantic search,
    key_prefix to enumerate a branch, subject_id for one person. Prefer this over re-reading moments
    when the question is about a durable attribute, preference, role or relationship.

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
        query: Semantic search over fact statements.
        key_prefix: List facts whose key starts with this. Keys are built from the canonical NAME,
            e.g. 'Matthew/' for everything known about Matthew, or 'event:' for all events; a
            person_id like 'P001/' also works and is mapped to that person's name. See
            get_memory_overview for the full key directory.
        subject_id: All facts whose subject is this person_id.
        top_k: Maximum number of matching results to return.
    """
    return [json_text(search_facts(**arguments))]
