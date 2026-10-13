#!/usr/bin/env python3
"""Merge per-video graph memories in a directory into one combined memory.

Usage:
    python merge_memories.py /path/to/video_dir
    python merge_memories.py /path/to/video_dir --output-dir /path/to/output
    python merge_memories.py /path/to/video_dir --retrieval-backend zvec
    python merge_memories.py /path/to/video_dir --skip-embeddings

Scans for *.mp4.memory/graph_memory.json, prefixes all IDs with a short
video key, concatenates everything, merges existing retrieval records without
re-embedding, and writes graph_memory.json plus the selected retrieval backend
in the video directory (or --output-dir).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import numpy as np
from embeddings import (
    RETRIEVAL_BACKENDS,
    EmbeddingIndex,
    detect_retrieval_index,
    export_retrieval_index,
    save_retrieval_index,
)


def _short_key(video_key: str) -> str:
    m = re.match(r"(P\d+)", video_key)
    if m:
        return m.group(1)
    stem = video_key.split("_")[0] if "_" in video_key else video_key
    return stem[:20]


def prefix_ids(data: dict, prefix: str):
    p = f"{prefix}_"

    for se in data.get("super_events", []):
        se["super_id"] = p + str(se["super_id"])
        se["sub_macro_ids"] = [p + str(mid) for mid in se.get("sub_macro_ids", [])]

    for me in data.get("macro_events", []):
        me["macro_id"] = p + str(me["macro_id"])
        me["super_id"] = p + str(me.get("super_id", "")) if me.get("super_id") else ""
        me["video_key"] = data.get("video_key", "")
        sg = me.get("subgraph")
        if sg:
            sg["macro_id"] = p + str(sg.get("macro_id", ""))
            for e in sg.get("micro_events", []):
                e["event_id"] = p + str(e.get("event_id", ""))
                e["macro_id"] = p + str(e.get("macro_id", "")) if e.get("macro_id") else ""
            for e in sg.get("entities", []):
                e["entity_id"] = p + str(e.get("entity_id", ""))
                e["macro_id"] = p + str(e.get("macro_id", "")) if e.get("macro_id") else ""
            for edge in sg.get("edges", []):
                edge["source_id"] = p + str(edge.get("source_id", ""))
                edge["target_id"] = p + str(edge.get("target_id", ""))
            for txt in sg.get("on_screen_texts", []):
                txt["text_id"] = p + str(txt.get("text_id", ""))
                txt["macro_id"] = p + str(txt.get("macro_id", "")) if txt.get("macro_id") else ""

    for mr in data.get("macro_relations", []):
        mr["source"] = p + str(mr["source"])
        mr["target"] = p + str(mr["target"])

    for sr in data.get("super_relations", []):
        sr["source"] = p + str(sr["source"])
        sr["target"] = p + str(sr["target"])


def _rebuild_embeddings_for_memory(gm_path: str):
    """Rebuild embeddings for a single video memory from its graph_memory.json."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from schema import HierarchicalGraphMemory

    memory = HierarchicalGraphMemory.load(gm_path)
    nodes = memory.get_all_nodes()
    if not nodes:
        return [], np.zeros((0, 0), dtype=np.float32)

    index = EmbeddingIndex()
    index.build(nodes)
    return index.nodes, index.embeddings


def merge_directory(
    video_dir: str,
    output_dir: str | None = None,
    skip_embeddings: bool = False,
    retrieval_backend: str = "npz",
):
    out = output_dir or video_dir
    os.makedirs(out, exist_ok=True)

    memory_files = []
    for entry in sorted(os.listdir(video_dir)):
        if not entry.endswith(".memory"):
            continue
        gm_path = os.path.join(video_dir, entry, "graph_memory.json")
        if os.path.exists(gm_path):
            memory_files.append((entry, gm_path))

    if not memory_files:
        print(f"No *.memory/graph_memory.json found in {video_dir}")
        return

    print(f"Merging {len(memory_files)} memories from {video_dir}")

    merged = {
        "video_key": os.path.basename(video_dir),
        "video_path": video_dir,
        "root": {"title": "", "description": "", "themes": [], "key_entities": [], "emotional_tone": ""},
        "super_events": [],
        "macro_events": [],
        "macro_relations": [],
        "super_relations": [],
        "source_videos": [],
    }

    titles = []
    all_themes = set()
    all_entities = set()
    prefix_map = {}  # entry -> prefix

    for entry, gm_path in memory_files:
        with open(gm_path) as f:
            data = json.load(f)

        video_key = data.get("video_key", entry.replace(".mp4.memory", ""))
        prefix = _short_key(video_key)
        prefix_map[entry] = prefix

        prefix_ids(data, prefix)

        merged["super_events"].extend(data.get("super_events", []))
        merged["macro_events"].extend(data.get("macro_events", []))
        merged["macro_relations"].extend(data.get("macro_relations", []))
        merged["super_relations"].extend(data.get("super_relations", []))

        root = data.get("root", {})
        if root.get("title"):
            titles.append(f"{prefix}: {root['title']}")
        all_themes.update(root.get("themes", []))
        all_entities.update(root.get("key_entities", []))

        merged["source_videos"].append(
            {
                "video_key": video_key,
                "prefix": prefix,
                "video_path": data.get("video_path", ""),
                "title": root.get("title", ""),
                "n_macros": len(data.get("macro_events", [])),
                "n_supers": len(data.get("super_events", [])),
            }
        )

        n = len(data.get("macro_events", []))
        print(f"  {prefix}: {n} macros, {len(data.get('super_events', []))} supers")

    merged["root"]["title"] = f"Merged memory: {len(memory_files)} videos"
    merged["root"]["description"] = "; ".join(titles[:20])
    if len(titles) > 20:
        merged["root"]["description"] += f" ... and {len(titles) - 20} more"
    merged["root"]["themes"] = sorted(all_themes)[:50]
    merged["root"]["key_entities"] = sorted(all_entities)[:100]

    gm_out = os.path.join(out, "graph_memory.json")
    with open(gm_out, "w") as f:
        json.dump(merged, f, ensure_ascii=False)
    print(f"\nSaved {gm_out}")
    print(f"  {len(merged['macro_events'])} macros, {len(merged['super_events'])} supers")

    if skip_embeddings:
        print("Skipping embedding merge (--skip-embeddings)")
        return

    # Merge retrieval records: reuse vectors from zvec or NPZ and rebuild only missing/corrupt inputs.
    print(f"\nMerging retrieval records into {retrieval_backend}...")
    t0 = time.time()
    all_nodes = []
    all_embs = []
    to_rebuild = []

    for entry, gm_path in memory_files:
        mem_dir = os.path.join(video_dir, entry)
        index_path = detect_retrieval_index(mem_dir)
        prefix = prefix_map[entry]
        p = f"{prefix}_"

        if not index_path:
            to_rebuild.append((entry, gm_path, prefix))
            continue

        try:
            nodes_json, embs = export_retrieval_index(index_path)
        except Exception as e:
            print(f"  {prefix}: retrieval index unreadable ({e}), will rebuild")
            to_rebuild.append((entry, gm_path, prefix))
            continue

        for node in nodes_json:
            node["node_id"] = p + str(node.get("node_id", ""))
            if node.get("macro_id"):
                node["macro_id"] = p + str(node["macro_id"])

        all_nodes.extend(nodes_json)
        all_embs.append(embs)

    # Rebuild corrupt/missing embeddings
    if to_rebuild:
        print(f"  Rebuilding {len(to_rebuild)} corrupt/missing retrieval indexes...")
        for entry, gm_path, prefix in to_rebuild:
            p = f"{prefix}_"
            try:
                nodes, embs = _rebuild_embeddings_for_memory(gm_path)
                for node in nodes:
                    node["node_id"] = p + str(node.get("node_id", ""))
                    if node.get("macro_id"):
                        node["macro_id"] = p + str(node["macro_id"])
                all_nodes.extend(nodes)
                if embs is not None and embs.size > 0:
                    all_embs.append(embs)
                print(f"    {prefix}: rebuilt {len(nodes)} nodes")
            except Exception as e:
                print(f"    {prefix}: rebuild failed ({e}), skipping")

    if all_embs:
        merged_embs = np.vstack(all_embs).astype(np.float32)
        index_out = save_retrieval_index(out, retrieval_backend, all_nodes, merged_embs)
        print(f"  Saved {index_out}: {len(all_nodes)} nodes, shape {merged_embs.shape} in {time.time() - t0:.1f}s")
    else:
        print("  WARNING: No embeddings to merge")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge per-video memories in a directory")
    parser.add_argument("video_dir", help="Directory containing *.mp4 files with .memory/ subdirs")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as video_dir)")
    parser.add_argument("--skip-embeddings", action="store_true", help="Skip embedding merge")
    parser.add_argument(
        "--retrieval-backend",
        choices=RETRIEVAL_BACKENDS,
        default="npz",
        help="Output retrieval backend (default: npz)",
    )
    args = parser.parse_args()

    merge_directory(args.video_dir, args.output_dir, args.skip_embeddings, args.retrieval_backend)
