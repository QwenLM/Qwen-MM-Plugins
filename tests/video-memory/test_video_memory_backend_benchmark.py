"""Focused tests for the video-memory storage backend benchmark."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).with_name("video_memory_backend.py")
_SPEC = importlib.util.spec_from_file_location("video_memory_backend_benchmark", _SCRIPT)
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def test_query_pack_uses_only_real_video_memory_search_routes():
    nodes = [
        {"node_id": "e1", "node_type": "entity", "macro_id": "m1", "text": "orange cat"},
        {"node_id": "v1", "node_type": "event", "macro_id": "m1", "text": "cat jumps"},
        {"node_id": "o1", "node_type": "on_screen_text", "macro_id": "m1", "text": "final score"},
        {"node_id": "a1", "node_type": "asr_text", "macro_id": "m1", "text": "spoken warning"},
    ]
    vectors = np.eye(4, dtype=np.float32)

    specs, query_vectors = benchmark._build_query_pack(nodes, vectors, max_queries=4)

    assert [spec["workload"] for spec in specs] == [
        "search_nodes",
        "search_ocr_text",
        "search_asr_text",
        "search_nodes",
    ]
    assert specs[0]["node_types"] == ["entity", "event"]
    assert specs[1]["node_types"] == ["on_screen_text"]
    assert specs[2]["node_types"] == ["asr_text"]
    assert query_vectors.shape == (4, 4)


def test_parity_treats_tie_reordering_as_set_alignment():
    npz = [[{"macro_id": "m", "node_id": "a"}, {"macro_id": "m", "node_id": "b"}]]
    zvec = [[{"macro_id": "m", "node_id": "b"}, {"macro_id": "m", "node_id": "a"}]]

    result = benchmark._parity(npz, zvec, top_k=2)

    assert result["top1_agreement"] == 0
    assert result["top2_set_overlap"] == 1
    assert result["top2_exact_order"] == 0


def test_benchmark_rejects_memory_without_retrieval_artifact(tmp_path):
    pytest.importorskip("zvec")

    with pytest.raises(RuntimeError, match="no embeddings.npz or embeddings.zvec"):
        benchmark.run_benchmark(str(tmp_path), max_queries=1, runs=1, top_k=1)


def test_scale_corpus_is_reproducible_and_preserves_source():
    nodes = [{"macro_id": "m1", "node_id": "n1", "text": "orange cat", "node_type": "event"}]
    vectors = np.ones((1, 4), dtype=np.float32)
    scaled_nodes, scaled_vectors = benchmark._scale_corpus(nodes, vectors, 3)
    repeated_nodes, repeated_vectors = benchmark._scale_corpus(nodes, vectors, 3)

    assert nodes[0]["node_id"] == "n1"
    np.testing.assert_array_equal(vectors, np.ones((1, 4), dtype=np.float32))
    assert len({node["node_id"] for node in scaled_nodes}) == 3
    assert all(node["text"] == "orange cat" for node in scaled_nodes)
    np.testing.assert_array_equal(scaled_vectors[0], vectors[0])
    assert scaled_nodes == repeated_nodes
    np.testing.assert_array_equal(scaled_vectors, repeated_vectors)


@pytest.mark.parametrize("backend", ["npz", "zvec"])
def test_diagnostics_observe_bm25_rebuild_only_for_npz(tmp_path, backend):
    if backend == "zvec":
        pytest.importorskip("zvec")
    nodes = [{"macro_id": "m1", "node_id": "n1", "text": "orange cat", "node_type": "event"}]
    vectors = np.ones((1, 4), dtype=np.float32)
    index_path = benchmark.save_retrieval_index(str(tmp_path / backend), backend, nodes, vectors)
    specs, queries = benchmark._build_query_pack(nodes, vectors, max_queries=1)
    query_pack = str(tmp_path / "queries.npz")
    np.savez(query_pack, specs=json.dumps(specs), vectors=queries)
    original = benchmark.EmbeddingIndex._build_sparse_index

    report = benchmark._diagnose_worker(index_path, query_pack, top_k=2)

    assert benchmark.EmbeddingIndex._build_sparse_index is original
    assert (report["npz_bm25_rebuild_ms"] > 0) == (backend == "npz")
    assert report["open_after_import_ms"] >= report["npz_bm25_rebuild_ms"]
    assert report["query_breakdown"]["hybrid"]["queries"] == 1
    assert report["query_breakdown"]["fts"]["calls"]
    if backend == "zvec":
        hybrid = report["query_breakdown"]["hybrid"]
        assert hybrid["mean_returned_candidates"] == {"embedding": 1, "text": 1}
        assert hybrid["calls"]["result_conversion"]["calls"] == 2
        assert hybrid["calls"]["metadata_fetch"]["calls"] == 1
        assert hybrid["calls"]["fetch_result_conversion"]["calls"] == 1
        assert hybrid["calls"]["native_query"]["calls"] == 2
        assert hybrid["calls"]["rrf_selection"]["calls"] == 1
        assert hybrid["calls"]["query_vector_setup"]["calls"] == 1
