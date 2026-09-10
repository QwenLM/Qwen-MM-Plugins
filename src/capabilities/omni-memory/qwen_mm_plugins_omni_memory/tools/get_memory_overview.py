from __future__ import annotations

from typing import Any

from shared.content import json_text

from ..service import load_store, memory_label
from . import MemoryRef


class GetMemoryOverviewArgs(MemoryRef):
    pass


TOOL = {"name": "get_memory_overview", "args": GetMemoryOverviewArgs}


def overview(video_path: str | None = None, namespace: str | None = None) -> dict[str, Any]:
    """The vocabulary a retrieval plan is written from.

    Structured counterpart to store.resident_context(), the block the pipeline puts in front of its
    planner: the cast of people, the directory of fact keys, and a marker that a scene container exists.

    Deliberately NOT here:
      · the scene items themselves — recalling them for every question measured net-negative, so they
        stay on demand via plan_and_search(include_scene=True)
      · counts and durations — get_memory_status covers those, and it runs first anyway
      · the rolling global summary — no producer in this pipeline, and net-negative for QA
    """
    store = load_store(video_path, namespace)
    return {
        "label": memory_label(video_path, namespace),
        "people": store.entity_directory(),
        "semantic_key_directory": store.semantic_key_directory() or [],
        "scene_env_available": bool(store.scene_env),
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """The vocabulary a retrieval plan is written from: the cast of people (person_id, name,
    appearance, and also_heard_as for anyone still unnamed) and the COMPLETE directory of semantic
    fact keys. Read this before plan_and_search — fact keys are an exact lookup, so a plan that
    names none comes back with no facts. One call covers every later question about the same video.
    `scene_env_available` only says a scene container exists; ask for the items with
    plan_and_search(include_scene=True).

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
    """
    return [json_text(overview(**arguments))]
