"""Focused tests for the video-memory storage backend benchmark."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).parents[1] / "benchmarks" / "video_memory_backend.py"
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
