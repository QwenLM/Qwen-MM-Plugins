"""Qwen-MM-Plugins omni-memory: stateful streaming memory for long videos.

One omni model reads each 30s window together with its in-video audio, so identities, dialogue and
speaker attribution come out of a single pass — there is no separate ASR or diarization stage.

This package is the READ side only; building lives in skill/script/build_memory/ (see SKILL.md).

  omni_core   what the build and the read path share: clients, throttling, vector codec, StoreBase
  mem_core    MemoryStore — the store plus hybrid retrieval over it
  watch       answering off the video itself: payload sizing, re-encoding, watch and replay calls
  text_match  tokenising, BM25 constants, cosine, RRF, phonetic name matching
  service     locating and caching a memory; what more than one tool needs
"""

import logging
import threading

from mcp_framework import build_registry

__version__ = "1.1.1"

log = logging.getLogger("qwen-mm-plugins-omni-memory")

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

# ffmpeg/ffprobe build a memory (window slicing) and back watch_and_answer, which re-encodes a video
# before sending it. Reading an existing memory never touches them, hence startup:False.
SYSTEM_DEPS = [
    {
        "label": "build memory: video probing and window slicing · watch_and_answer: re-encoding",
        "extra": "omni-memory",
        "tools": ["ffmpeg", "ffprobe"],
        "hint": "apt install ffmpeg   |   brew install ffmpeg",
        "startup": False,
    },
]
SYSTEM_DEPS_NOTE = "  Querying an already-built memory needs no system tools."

USAGE_NOTE = (
    "Pass video_path to every tool. For a named memory, also pass namespace unless MEM_LOCAL_DIR "
    "is configured.\n"
    "DASHSCOPE_API_KEY enables dense retrieval; without it, search falls back to keyword-only\n"
    "ranking. The shared DashScope endpoint and Omni model settings are used to build a memory and for the\n"
    "two tools that watch video: replay_and_answer and watch_and_answer.\n"
    "watch_and_answer takes video_path only — it needs no memory to exist."
)


def on_start() -> None:
    from . import service

    log.info("Starting omni-memory MCP server (query-only; build lives in skill/script)")
    log.info("  MEM_LOCAL_DIR=%s", service.memory_root() or "<input video directory>")
    if service.ENV_TUNING:
        # Uncatalogued MEM_* knobs change retrieval or determinism without surfacing anywhere else.
        log.info("  from the environment: %s", " ".join(f"{k}={v}" for k, v in service.ENV_TUNING.items()))
    # Warm the store cache in the background when a fixed memory root is configured.
    threading.Thread(target=service.preload, daemon=True).start()
