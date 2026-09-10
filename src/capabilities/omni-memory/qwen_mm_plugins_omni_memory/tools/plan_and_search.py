from __future__ import annotations

from typing import Any

from pydantic import Field

from shared.content import json_text

from ..service import embed_client, load_store, memory_label, moment_brief
from . import MemoryRef


class PlanAndSearchArgs(MemoryRef):
    question: str = ""
    people: list[str] | None = None
    fact_keys: list[str] | None = None
    queries: list[str] | None = None
    time_ranges: list[list[float]] | None = None
    include_scene: bool = False
    top_k: int = Field(default=5, ge=1, le=20)


TOOL = {"name": "plan_and_search", "args": PlanAndSearchArgs}


def plan_and_search(
    video_path: str | None = None,
    namespace: str | None = None,
    question: str = "",
    people: list[str] | None = None,
    fact_keys: list[str] | None = None,
    queries: list[str] | None = None,
    time_ranges: list[list[float]] | None = None,
    include_scene: bool = False,
    top_k: int = 5,
) -> dict[str, Any]:
    """Execute a retrieval plan the CALLER decided on, fusing the containers in one call.

    ONE search per question, the way the pipeline this comes from did it. Read get_memory_overview
    first: it hands over the person roster and the fact-key directory, which is what the original
    planner was shown before it planned. Facts here are an exact key lookup, so a plan naming no keys
    gets no facts — that is why the directory comes first rather than being guessed at. Scene items
    stay on demand (`include_scene`): recalling them for every question measured net-negative.

    The answering is yours.
    """
    store = load_store(video_path, namespace)
    given_keys = list(fact_keys or [])
    episode_queries = list(queries or ([question] if question else []))
    # SCENE_ENV rides in need_keys because that is how run_plan is told to recall the environment;
    # it is not itself a semantic key, so it is reported back as the flag the caller actually set.
    keys = [*given_keys, "SCENE_ENV"] if (include_scene and "SCENE_ENV" not in given_keys) else given_keys
    plan = {
        "need_entities": list(people or []),
        "need_keys": keys,
        "episode_queries": episode_queries,
        "time_ranges": [list(t) for t in (time_ranges or [])],
    }
    hits = store.run_plan(plan, question or " ".join(episode_queries), client=embed_client(), k=max(1, int(top_k)))
    replays = [c for c in (hits.get("replays") or []) if c]
    return {
        "label": memory_label(video_path, namespace),
        "plan_used": {
            "people": list(people or []),
            "fact_keys": given_keys,
            "queries": episode_queries,
            "time_ranges": [list(t) for t in (time_ranges or [])],
            "include_scene": bool(include_scene),
        },
        "people": [
            {"person_id": e.get("person_id"), "name": e.get("name"), "appearance": (e.get("appearance") or "")[:120]}
            for e in (hits.get("entities") or [])
        ],
        "facts": [
            {"key": t.get("key"), "statement": t.get("statement"), "confidence": t.get("confidence")}
            for t in (hits.get("semantic") or [])
        ],
        "moments": [moment_brief(store, o) for o in (hits.get("episodic") or [])],
        "scene_env": list(hits.get("scene") or []),
        # Clips the retrieval considers most relevant — hand these to replay_and_answer if the text
        # evidence turns out to be insufficient.
        "suggested_replay_idxs": [c.get("idx") for c in replays],
        "evidence_text": store.evidence_text(hits),
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Run ONE retrieval from a plan you decide, fusing the memory containers: people, stable facts,
    moments and (on request) environment, plus a ready-to-read evidence text and the clips worth re-
    watching. **This does not answer the question — it returns evidence for you to reason over.**
    Call get_memory_overview first: naming the right person_ids, fact keys and query angles is what
    makes the recall precise, and the keys are an exact lookup. Use this for open-ended questions;
    for a targeted one go straight to the matching tool (search_dialogue, search_facts,
    get_person_dialogue, get_timeline, …).

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
        question: The question you are trying to answer.
        people: person_ids whose dossier and moments are relevant, e.g. ['P001']. Take them from
            get_memory_overview. A person whose name is still null may carry `also_heard_as` — that
            is how a name someone says maps onto an anonymous id.
        fact_keys: Semantic keys to pull, e.g. ['Matthew/role']. EXACT lookup — pick them from
            get_memory_overview's semantic_key_directory. Name none and no facts come back; to find
            facts by content rather than by key, use search_facts.
        queries: Descriptive statements (not questions) to search moments with — 'someone offers to
            bring an umbrella', not 'who brought an umbrella?'. Several angles beat one long query.
            These also decide which clips come back in suggested_replay_idxs, so describe what to
            look for.
        time_ranges: Optional [[start_sec, end_sec], ...] if the question concerns a time span.
        include_scene: Also recall environment/layout items. Turn it on for 'where is X / what is in
            the room' questions — it is off by default because recalling scene for every question
            measured net-negative, so it is a deliberate pick rather than a freebie.
        top_k: Maximum number of matching results to return.
    """
    return [json_text(plan_and_search(**arguments))]
