from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.content import json_text

from .. import watch
from ..service import library_namespaces, memory_dir, memory_label, memory_root
from . import MemoryRef


class GetMemoryStatusArgs(MemoryRef):
    pass


TOOL = {"name": "get_memory_status", "args": GetMemoryStatusArgs}


_CLIP_DIRNAME = "clips"


def _quick_info(mdir: Path) -> dict[str, Any]:
    """Cheap header read — avoids hydrating the whole store just to list it."""
    d = json.loads((mdir / "store.json").read_text(encoding="utf-8"))
    ep = d.get("episodic") or []
    ents = d.get("entities") or []
    return {
        "clips": len(d.get("clips") or []),
        "episodic": len(ep),
        "people": len(ents),
        "named_people": sum(1 for e in ents if (e or {}).get("name")),
        "semantic_facts": len(d.get("semantic") or []),
        "duration_sec": round(max((c.get("win_end", 0) for c in (d.get("clips") or [])), default=0), 1),
    }


def _no_memory_next_step(video_path: str | None, duration_sec: float) -> str:
    """What to do when there is no memory yet, given how long the video is.

    Building is not always the answer: an omni call per 30s window is poor advice for a two-minute
    clip. An unknown duration falls back to suggesting a build, which is never wrong, only sometimes
    more expensive than it needed to be.
    """
    build = f"build it: python3 script/build_memory/build_memory.py {video_path or '<video>'}"
    if not duration_sec:
        return build
    mins = duration_sec / 60
    if mins <= watch.WATCH_PREFER_MIN:
        return (
            f"only {mins:.1f} min — watch_and_answer(video_path, question) answers it in one call, no "
            f"memory needed. Build one instead if you expect several questions about this video: {build}"
        )
    if mins <= watch.WATCH_MAX_MIN:
        return (
            f"{mins:.1f} min — {build}. Use watch_and_answer only if the user has said they do not want "
            f"a memory and just wants a quick answer"
        )
    return f"{mins:.1f} min, past the {watch.WATCH_MAX_MIN:.0f} min watch limit — {build}"


def _planned_clip_count(mdir: Path) -> int | None:
    """How many clips the slice plan expected, or None when there is no plan to read.

    Clips live at <mdir>/clips/<fingerprint>/<video_stem>/plan.json. A streamed memory holds several
    videos appended into one timeline, so all plans under the SAME fingerprint are summed. When
    several fingerprints exist (slicing parameters were changed at some point), the most recently
    written one is the live layout.
    """
    cdir = mdir / _CLIP_DIRNAME
    if not cdir.is_dir():
        return None
    fps = [d for d in cdir.iterdir() if d.is_dir()]
    if not fps:
        return None
    fps.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    total = 0
    for p in sorted(fps[0].glob("*/plan.json")):
        try:
            total += len(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None
    return total or None


def memory_status(video_path: str | None = None, namespace: str | None = None) -> dict[str, Any]:
    """Existence + metadata + INTEGRITY check.

    An interrupted build still finalizes its library, so a clip that exhausted its retries leaves one
    that looks normal but is truncated. Comparing episodic count against the slice plan is the only way
    to see it.
    """
    mdir = memory_dir(video_path, namespace)
    label = memory_label(video_path, namespace)
    if not (mdir / "store.json").is_file():
        out: dict[str, Any] = {"exists": False, "label": label, "memory_dir": str(mdir)}
        # Report how long the source is, and let that pick the next step. Without this the caller has
        # no way to apply the routing bands at all: duration is otherwise only known once a memory
        # exists, which is after the decision it informs. ffprobe is one subprocess and stays
        # optional — when it is missing the field is simply absent and building is the safe advice.
        dur = watch.probe_media(video_path)["duration"] if video_path else 0.0
        if dur:
            out["duration_sec"] = round(dur, 1)
            out["duration_min"] = round(dur / 60, 1)
        out["next_step"] = _no_memory_next_step(video_path, dur)
        # Nothing here — surface what the shared library root does have, so a wrong/stale namespace
        # is recoverable without a separate listing tool.
        others = library_namespaces()
        if others:
            out["available_in_library"] = others[:40]
            out["library_root"] = memory_root()
        return out
    info = _quick_info(mdir)
    # Two independent baselines for "how many clips should there be":
    #   · plan.json from the slice cache — authoritative, but absent for memories built against an
    #     external --clips-dir
    #   · store.clips — written up-front for the whole video at slice time, while store.episodic
    #     only grows as clips are successfully extracted, so a gap between them IS the truncation
    # Falling back to store.clips matters: without it a 5/73 library reports complete=true, which is
    # the exact failure this tool exists to catch.
    planned = _planned_clip_count(mdir) or (info["clips"] or None)
    complete = planned is None or info["episodic"] >= planned
    out = {"exists": True, "label": label, "memory_dir": str(mdir), "complete": complete, **info}
    if planned is not None:
        out["planned_clips"] = planned
    if not complete:
        out["truncated"] = f"{info['episodic']}/{planned} clips extracted"
        out["next_step"] = (
            "this memory is INCOMPLETE and its answers cannot be trusted — resume it: "
            "python3 script/build_memory/build_memory.py <video> --mode resume"
        )
    return out


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Does a memory exist, is it COMPLETE, and does this video even need one? Call this before
    anything else: it tells you whether to build, resume an interrupted build, watch the video
    directly instead, or start querying. An interrupted build leaves a library that looks normal but
    is truncated, so the extracted-clip count is checked against the slice plan. When no memory
    exists it also reports the source video's `duration_min` and a `next_step` that applies the
    length routing — short videos are answered by watch_and_answer without building anything.

    Args:
        video_path: Absolute path to the source video; memory is read from <video_path>.memory/.
        namespace: Memory name. Pass video_path too when MEM_LOCAL_DIR is unset; otherwise the
            memory is read from the configured shared root.
    """
    return [json_text(memory_status(**arguments))]
