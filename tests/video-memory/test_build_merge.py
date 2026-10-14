"""Unit test for build_graph.merge_chunks (C1).

The parallel-chunk merge (global re-sort by time + contiguous macro-id remap + subgraph
rename) used to live only inside a bash heredoc in build_memory.sh — un-importable and
untested. It's now a function in build_graph.py; this locks its behaviour.
"""

import json
import os
import sys

import pytest
from conftest import REPO_ROOT

_BUILD_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "video-memory", "skill", "script", "build_memory")


@pytest.fixture(scope="module")
def build_graph():
    sys.path.insert(0, _BUILD_DIR)
    try:
        import build_graph as bg
    except Exception as e:  # noqa: BLE001 — heavy build-only deps may be absent
        pytest.skip(f"build_graph not importable: {e}")
    return bg


@pytest.fixture(scope="module")
def merge_memories():
    pytest.importorskip("numpy")
    pytest.importorskip("zvec")
    if _BUILD_DIR not in sys.path:
        sys.path.insert(0, _BUILD_DIR)
    try:
        import merge_memories as mm
    except Exception as e:  # noqa: BLE001 — optional build-only deps may be absent
        pytest.skip(f"merge_memories not importable: {e}")
    return mm


def _write_chunk(root, name, macros):
    d = root / name
    (d / "subgraphs").mkdir(parents=True)
    (d / "01_macros.json").write_text(json.dumps(macros))
    for m in macros:
        (d / "subgraphs" / f"{m['macro_id']}.json").write_text(
            json.dumps({"macro_id": m["macro_id"], "micro_events": []})
        )
    return str(d)


def test_merge_chunks_resorts_remaps_and_renames(build_graph, tmp_path):
    # Two chunks each number their macros from macro_0000; chunk_01 covers later time.
    c0 = _write_chunk(
        tmp_path,
        "chunk_00",
        [
            {"macro_id": "macro_0000", "label": "a", "time_range": [0, 10]},
            {"macro_id": "macro_0001", "label": "b", "time_range": [10, 20]},
        ],
    )
    c1 = _write_chunk(tmp_path, "chunk_01", [{"macro_id": "macro_0000", "label": "c", "time_range": [20, 30]}])
    out = tmp_path / "out"
    out.mkdir()

    n = build_graph.merge_chunks([c1, c0], str(out))  # deliberately out of order
    assert n == 3

    merged = json.loads((out / "01_macros.json").read_text())
    # nothing dropped or duplicated: merged count matches the reported count and the inputs
    assert len(merged) == n == 3
    # globally sorted by time_range start, ids reassigned contiguously
    assert [m["macro_id"] for m in merged] == ["macro_0000", "macro_0001", "macro_0002"]
    assert [m["label"] for m in merged] == ["a", "b", "c"]
    # time ranges preserved verbatim and globally non-overlapping/ascending after the re-sort
    assert [m["time_range"] for m in merged] == [[0, 10], [10, 20], [20, 30]]
    starts = [m["time_range"][0] for m in merged]
    assert starts == sorted(starts)

    # each subgraph copied under its NEW id, with the inner macro_id rewritten to match
    for i in range(3):
        nid = f"macro_{i:04d}"
        sg = json.loads((out / "subgraphs" / f"{nid}.json").read_text())
        assert sg["macro_id"] == nid
    # exactly one subgraph file per merged macro — no stale/leftover chunk-local names
    assert sorted(p.name for p in (out / "subgraphs").iterdir()) == [f"macro_{i:04d}.json" for i in range(3)]


def _write_memory(root, mm, key, text, vector, backend="zvec"):
    memory_dir = root / f"{key}.mp4.memory"
    memory_dir.mkdir()
    graph = {
        "video_key": key,
        "video_path": str(root / f"{key}.mp4"),
        "root": {"title": key, "description": "", "themes": [], "key_entities": [], "emotional_tone": ""},
        "super_events": [],
        "macro_events": [{"macro_id": "macro_0000", "label": text, "time_range": [0, 1]}],
        "macro_relations": [],
        "super_relations": [],
    }
    (memory_dir / "graph_memory.json").write_text(json.dumps(graph))
    mm.save_retrieval_index(
        str(memory_dir),
        backend,
        [{"node_id": "duplicate:id", "node_type": "event", "macro_id": "macro_0000", "text": text}],
        mm.np.asarray([vector], dtype=mm.np.float32),
    )


def test_merge_directory_can_write_one_zvec_memory(merge_memories, tmp_path):
    _write_memory(tmp_path, merge_memories, "P001", "orange cat", [1.0, 0.0])
    _write_memory(tmp_path, merge_memories, "P002", "blue car", [0.0, 1.0])
    output = tmp_path / "merged"
    output.mkdir()
    (output / "embeddings.npz").write_bytes(b"stale")

    merge_memories.merge_directory(str(tmp_path), str(output), retrieval_backend="zvec")

    index_path = output / "embeddings.zvec"
    assert index_path.is_dir()
    assert not (output / "embeddings.npz").exists()
    nodes, matrix = merge_memories.export_retrieval_index(str(index_path))
    assert sorted(node["node_id"] for node in nodes) == ["P001_duplicate:id", "P002_duplicate:id"]
    assert sorted(node["macro_id"] for node in nodes) == ["P001_macro_0000", "P002_macro_0000"]
    assert matrix.shape == (2, 2)

    graph = json.loads((output / "graph_memory.json").read_text())
    assert [video["prefix"] for video in graph["source_videos"]] == ["P001", "P002"]


def test_merge_mixed_input_backends_can_write_npz(merge_memories, tmp_path):
    _write_memory(tmp_path, merge_memories, "P001", "orange cat", [1.0, 0.0], backend="zvec")
    _write_memory(tmp_path, merge_memories, "P002", "blue car", [0.0, 1.0], backend="npz")
    output = tmp_path / "merged"

    merge_memories.merge_directory(str(tmp_path), str(output), retrieval_backend="npz")

    index_path = output / "embeddings.npz"
    assert index_path.is_file()
    assert not (output / "embeddings.zvec").exists()
    nodes, matrix = merge_memories.export_retrieval_index(str(index_path))
    assert sorted(node["node_id"] for node in nodes) == ["P001_duplicate:id", "P002_duplicate:id"]
    assert matrix.shape == (2, 2)


def test_merge_rejects_invalid_backend_before_writing_output(merge_memories, tmp_path):
    output = tmp_path / "merged"

    with pytest.raises(ValueError, match="unsupported retrieval backend"):
        merge_memories.merge_directory(str(tmp_path), str(output), retrieval_backend="invalid")

    assert not output.exists()
