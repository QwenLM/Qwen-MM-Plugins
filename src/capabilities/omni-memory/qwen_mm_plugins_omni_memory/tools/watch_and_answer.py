from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared.content import json_text

from .. import omni_core, watch
from ..service import memory_dir


class WatchAndAnswerArgs(BaseModel):
    """Not a MemoryRef: this is the one tool that reads the video itself, so it takes a source path
    and never a namespace — there is no memory to locate."""

    video_path: str
    question: str
    model: str | None = None


TOOL = {"name": "watch_and_answer", "args": WatchAndAnswerArgs}


_MP4_LIKE = {".mp4", ".m4v"}


def _watch_cache_path(video: Path, plan: dict[str, Any]) -> Path:
    """Where the re-encoded copy of `video` lives, named after the profile that produced it.

    The profile goes in the filename, as build_memory's clip cache does, so a file encoded under a
    different budget is not reused as if it matched. It lives under the memory directory because a failed
    watch falls back to building there anyway; get_memory_status keys on store.json, so a directory
    holding only this cache still reports exists: false.
    """
    name = f"h{plan['height']}_v{plan['v_kbps']}k_a{plan['a_kbps']}k.mp4"
    return memory_dir(video_path=str(video)) / "watch" / name


def _watch_source(video: Path, info: dict[str, Any]) -> tuple[Path, bool, dict[str, Any]]:
    """Return (playable path, was_cached, plan). Raises RuntimeError when nothing fits.

    `info` is the caller's probe, passed in so one ffprobe serves both the duration guard and the
    encode decision.

    A source that already fits the payload budget, is no taller than the top rung and is already an mp4
    is sent untouched; re-encoding could only inflate it and double-compress the picture.

    Cache freshness is the make rule (cache newer than source): no sidecar, and it invalidates itself
    when the video is replaced in place.
    """
    plan = watch.plan_watch_encode(info)
    if plan.get("error"):
        raise RuntimeError(plan["error"])
    if plan.get("reuse") and video.suffix.lower() in _MP4_LIKE:
        return video, True, {**plan, "source_used_directly": True}
    if plan.get("reuse"):
        # Fits, but the container is not one the data URI can honestly label as video/mp4. Remux at the
        # source's own rates so the only thing that changes is the wrapper.
        plan = {"height": info["height"] or 480, "v_kbps": info["v_kbps"] or 500, "a_kbps": info["a_kbps"] or 32}
    cache = _watch_cache_path(video, plan)
    if cache.is_file() and cache.stat().st_size > 0 and cache.stat().st_mtime >= video.stat().st_mtime:
        return cache, True, plan
    watch.transcode_whole(str(video), str(cache), plan["height"], f"{plan['v_kbps']}k", f"{plan['a_kbps']}k")
    return cache, False, plan


def watch_and_answer(
    video_path: str = "",
    question: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    """Watch a whole short video in ONE omni call and answer from it. No memory required.

    The only tool here that needs no memory, which works only because the video is short enough for one
    request. It re-encodes first so the payload follows duration rather than the source's bitrate, and
    keeps that copy so repeated questions skip the re-encode.

    A hard failure comes back with `fallback: "build_memory"` and the command to run. Throttling does
    not: it is transient, and a build would spend tens of minutes working around it.
    """
    if not video_path:
        return {"error": "video_path is required"}
    video = Path(video_path).expanduser()
    if not video.is_file():
        return {"error": f"video not found: {video}"}
    if not question:
        return {"error": "question is required"}
    video = video.resolve()
    build_cmd = f"python3 script/build_memory/build_memory.py {video}"

    # One probe serves both the length guard and the encode decision. Duration is checked first, since
    # it rules out a video that re-encoding would spend minutes on; it is 0 when ffprobe is unavailable,
    # leaving the encoded-size guard as the only backstop.
    info = watch.probe_media(str(video))
    duration = info["duration"]
    if duration and duration / 60 > watch.WATCH_MAX_MIN:
        return {
            "video_path": str(video),
            "duration_sec": round(duration, 1),
            "duration_min": round(duration / 60, 1),
            "error": f"{duration / 60:.1f} min is past the {watch.WATCH_MAX_MIN:.0f} min limit for "
            "watching in one call",
            "fallback": "build_memory",
            "next_step": build_cmd,
            "hint": "too long to watch — build a memory, then query it with plan_and_search",
        }

    try:
        src, cached, plan = _watch_source(video, info)
    except Exception as e:
        return {
            "error": f"could not prepare the video for watching: {str(e)[:300]}",
            "video_path": str(video),
            "fallback": "build_memory",
            "next_step": build_cmd,
            "hint": "watching needs ffmpeg on PATH; if it is missing, a build needs it too",
        }

    nbytes = src.stat().st_size
    sent_mb = round(nbytes * 4 / 3 / 1048576, 1)  # inline base64 is 4/3 of the file
    common: dict[str, Any] = {
        "video_path": str(video),
        "sent_mb": sent_mb,
        "transcode_cached": cached,
        "profile": "source as-is"
        if plan.get("source_used_directly")
        else f"{plan['height']}p {plan['v_kbps']}k / {plan['a_kbps']}k mono",
    }
    if duration:
        common["duration_sec"] = round(duration, 1)
        common["duration_min"] = round(duration / 60, 1)

    # No size check of our own: the endpoint enforces its own limit, and "Exceeded limit on max bytes
    # per data-uri item" arrives as a 400 that classifies as `reject` and takes the build fallback below.
    # Guessing the ceiling could only refuse requests that would have worked. The encode is still sized
    # against a byte budget, since a target bitrate has to come from somewhere — sizing is not refusing.
    try:
        answer = watch.watch_answer(omni_core.get_client(), omni_core.b64_uri(str(src)), question, model_override=model)
    except watch.WatchError as e:
        out = {**common, "error": str(e), "failure": e.kind}
        # Only failures a build would get past are answered with a build: throttling is transient, and
        # a bad endpoint or key would break the build the same way.
        if not e.build_helps:
            out["hint"] = (
                "the endpoint is throttling — retry shortly; this is not a size problem"
                if e.transient
                else "the omni endpoint, model, or credentials are wrong (DASHSCOPE_BASE_URL / "
                "QWEN_MM_API_OMNI_MODEL / DASHSCOPE_API_KEY) — a build would fail the same way, "
                "so fix the configuration first"
            )
            return out
        out["fallback"] = "build_memory"
        out["next_step"] = build_cmd
        out["hint"] = "direct watching did not get through — build a memory, then query it with plan_and_search"
        return out

    res = {**common, "question": question, "answer": answer, "model": model or omni_core.MODEL}
    if (memory_dir(video_path=str(video)) / "store.json").is_file():
        res["note"] = "a memory already exists for this video — plan_and_search answers follow-ups without re-uploading"
    return res


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Watch a SHORT audio-video in one pass and answer from it, with NO memory involved. Use it when
    the video is under ~10 minutes, or when the user has said they do not want a memory built and
    just wants a quick answer. Past ~30 minutes build a memory instead — this tool sends the whole
    video in a single request, so it cannot cover a long one no matter how the question is phrased.
    The video is re-encoded once and that copy is kept, so follow-up questions about the same video
    skip the re-encode. If the video will be asked about several times, a memory is cheaper: build
    it and use plan_and_search. When a watch cannot get through, the result carries
    fallback="build_memory" and the exact command to run. Needs ffmpeg on PATH and an omni endpoint.

    Args:
        video_path: Absolute path to the source video. No memory needs to exist.
        question: What to determine from watching the video.
        model: Omni model for this call. Leave unset to use the configured default (qwen3.5-omni-
            plus).
    """
    return [json_text(watch_and_answer(**arguments))]
