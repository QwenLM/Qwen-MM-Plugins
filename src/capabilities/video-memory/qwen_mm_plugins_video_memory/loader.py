"""MemoryToolkit loader + cache for the video-memory MCP tools."""

from __future__ import annotations

import logging
import os
import threading

from shared.env import get_env, get_float_env

from .embeddings import EmbeddingIndex
from .schema import HierarchicalGraphMemory
from .time_system import detect_time_system
from .toolkit import MemoryToolkit

log = logging.getLogger("qwen-mm-plugins-video-memory")

# Env is read at CALL time (not import) via the shared accessor, so a value set after
# import still applies and video-memory follows the same single-accessor convention.
# Cache entries include every input that changes the loaded toolkit. Replacing the graph or
# embeddings, selecting a different path, or changing the cutoff invalidates the entry.
_PathStamp = tuple[str, int, int]
_CacheSignature = tuple[_PathStamp, _PathStamp, float | None]
_toolkits: dict[str, tuple[_CacheSignature, MemoryToolkit]] = {}
_toolkits_lock = threading.Lock()


def _path_stamp(path: str) -> _PathStamp:
    path = os.path.realpath(path)
    try:
        stat = os.stat(path)
        return path, stat.st_mtime_ns, stat.st_size
    except OSError:
        return path, 0, 0


def _resolve_embed_path(memory_dir: str, configured: str | None) -> str:
    if configured:
        configured = os.path.realpath(configured)
        if os.path.exists(configured):
            return configured
    default = os.path.realpath(os.path.join(memory_dir, "embeddings.npz"))
    return default if os.path.exists(default) or not configured else configured


def get_toolkit(video_path: str | None = None) -> MemoryToolkit:
    """Resolve + cache the MemoryToolkit for a memory dir.

    Resolution order:
    1. GRAPH_MEMORY_PATH env var (explicit override)
    2. Directory-level merged memory: <video_dir>/graph_memory.json
       (when video_path is a dir, or the parent dir of a video file has one)
    3. Per-video memory: <video_path>.memory/graph_memory.json
    """
    graph_env = get_env("GRAPH_MEMORY_PATH")
    if graph_env:
        graph_path = graph_env
        memory_dir = os.path.dirname(graph_path)
    elif video_path:
        if os.path.isdir(video_path):
            dir_gm = os.path.join(video_path, "graph_memory.json")
            if os.path.exists(dir_gm):
                graph_path = dir_gm
                memory_dir = video_path
            else:
                raise RuntimeError(f"No merged graph_memory.json in {video_path}. Run merge_memories.py to create one.")
        else:
            parent_dir = os.path.dirname(video_path)
            dir_gm = os.path.join(parent_dir, "graph_memory.json")
            if os.path.exists(dir_gm):
                graph_path = dir_gm
                memory_dir = parent_dir
            else:
                memory_dir = f"{video_path}.memory"
                graph_path = os.path.join(memory_dir, "graph_memory.json")
    else:
        raise RuntimeError(
            "No video_path provided and GRAPH_MEMORY_PATH not set. "
            "Either pass video_path in the tool call or set GRAPH_MEMORY_PATH."
        )

    memory_dir = os.path.realpath(memory_dir)
    graph_path = os.path.realpath(graph_path)
    embed_path = _resolve_embed_path(memory_dir, get_env("EMBEDDINGS_PATH") or None)
    cutoff_value = get_float_env("CUTOFF_SEC")
    signature = (_path_stamp(graph_path), _path_stamp(embed_path), cutoff_value)
    cached = _toolkits.get(memory_dir)
    if cached is not None and cached[0] == signature:
        return cached[1]

    with _toolkits_lock:
        cached = _toolkits.get(memory_dir)
        if cached is not None and cached[0] == signature:
            return cached[1]
        return _load_toolkit_into(memory_dir, graph_path, embed_path, cutoff_value, signature)


def load_toolkit(
    memory_dir: str,
    graph_path: str | None = None,
    *,
    embed_path: str | None = None,
    cutoff: str | float | None = None,
) -> MemoryToolkit:
    """Build a MemoryToolkit from a memory dir (uncached), applying the SAME time-system
    rebasing and embedding-index loading the server uses — so the CLI and the MCP
    tools resolve identical answers for the same memory. `graph_path` defaults to
    `<memory_dir>/graph_memory.json`; `embed_path` to `<memory_dir>/embeddings.npz`."""
    graph_path = graph_path or os.path.join(memory_dir, "graph_memory.json")
    if not os.path.exists(graph_path):
        raise RuntimeError(
            f"Graph memory not found: {graph_path}. Please run `bash src/capabilities/video-memory/skill/script/build_memory/build_memory.sh` to build memory first!"
        )

    log.info("Loading graph memory from %s", graph_path)
    memory = HierarchicalGraphMemory.load(graph_path)

    ts = detect_time_system(memory)
    if ts.mode_name != "default":
        patched = ts.patch_offsets(memory)
        log.info("Auto-detected %s data, patched %d macros", ts.mode_name, patched)

    if not embed_path or not os.path.exists(embed_path):
        default = os.path.join(memory_dir, "embeddings.npz")
        embed_path = default if os.path.exists(default) else ""

    index = None
    if embed_path:
        log.info("Loading embedding index from %s", embed_path)
        index = EmbeddingIndex()
        index.load(embed_path)
        try:
            index.check_dimension_compatibility()
        except Exception as e:
            index._dense_disabled = True
            log.warning("Embedding dimension check failed, dense search disabled (BM25-only fallback): %s", e)
    else:
        log.warning("No embeddings found, search_nodes will be unavailable")

    toolkit = MemoryToolkit(memory, index, egolife_mode=(ts.mode_name == "egolife"))
    if cutoff is not None:
        toolkit.set_cutoff(float(cutoff))
        log.info("Time cutoff set to %s seconds", cutoff)

    log.info("MemoryToolkit loaded: %d macros, %d supers", len(memory.macro_events), len(memory.super_events))
    return toolkit


def _load_toolkit_into(
    memory_dir: str,
    graph_path: str,
    embed_path: str,
    cutoff: float | None,
    signature: _CacheSignature,
) -> MemoryToolkit:
    """Load (via the shared `load_toolkit`) and cache the toolkit for `memory_dir`."""
    toolkit = load_toolkit(memory_dir, graph_path, embed_path=embed_path, cutoff=cutoff)
    _toolkits[memory_dir] = (signature, toolkit)
    return toolkit


def preload() -> None:
    """Best-effort background pre-load when GRAPH_MEMORY_PATH is set (skipped otherwise)."""
    try:
        if get_env("GRAPH_MEMORY_PATH"):
            get_toolkit()
            log.info("Toolkit pre-loaded successfully")
        else:
            log.info("No GRAPH_MEMORY_PATH set, skipping pre-load (will load on first tool call via video_path)")
    except Exception as e:
        log.warning("Toolkit pre-load failed (will retry on first tool call): %s", e)
