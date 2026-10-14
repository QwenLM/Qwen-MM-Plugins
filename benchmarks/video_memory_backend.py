#!/usr/bin/env python3
"""Compare the retrieval storage backends used by video-memory.

The benchmark deliberately stays below the MCP and remote-embedding layers. It exports one
existing memory, writes the exact same nodes and vectors to NPZ and zvec, then measures the two
runtime paths the project actually uses: hybrid RRF and the FTS-only fallback.

Example:
    python benchmarks/video_memory_backend.py /path/to/video.mp4.memory
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "capabilities" / "video-memory"))
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402

from qwen_mm_plugins_video_memory.embeddings import (  # noqa: E402
    ZvecEmbeddingIndex,
    _tokenize,
    detect_retrieval_index,
    export_retrieval_index,
    load_retrieval_index,
    save_retrieval_index,
)

_WORKLOAD_ORDER = ("search_nodes", "search_ocr_text", "search_asr_text")
_TOOL_OVERFETCH = 2


def _filters_for_node_type(node_type: str) -> tuple[str, list[str]] | None:
    if node_type in {"entity", "event"}:
        return "search_nodes", ["entity", "event"]
    if node_type == "on_screen_text":
        return "search_ocr_text", ["on_screen_text"]
    if node_type == "asr_text":
        return "search_asr_text", ["asr_text"]
    return None


def _build_query_pack(
    nodes: list[dict],
    embeddings: np.ndarray,
    max_queries: int,
) -> tuple[list[dict], np.ndarray]:
    """Build deterministic, workload-balanced queries without calling an embedding API."""
    token_sets = [set(_tokenize(str(node.get("text", "")))) for node in nodes]
    document_frequency = collections.Counter(token for tokens in token_sets for token in tokens)
    candidates: dict[str, list[tuple[dict, np.ndarray]]] = {name: [] for name in _WORKLOAD_ORDER}
    seen: set[tuple[str, str]] = set()

    for position, (node, tokens) in enumerate(zip(nodes, token_sets, strict=True)):
        route = _filters_for_node_type(str(node.get("node_type", "")))
        if route is None or not tokens:
            continue
        workload, node_types = route
        selected = sorted(tokens, key=lambda token: (document_frequency[token], token))[:2]
        query = " ".join(selected)
        identity = (workload, query)
        if not query or identity in seen:
            continue
        seen.add(identity)
        candidates[workload].append(
            (
                {
                    "query": query,
                    "node_types": node_types,
                    "workload": workload,
                    "source": {
                        "macro_id": str(node.get("macro_id", "")),
                        "node_id": str(node.get("node_id", "")),
                    },
                },
                np.asarray(embeddings[position], dtype=np.float32),
            )
        )

    specs: list[dict] = []
    vectors: list[np.ndarray] = []
    offsets = {name: 0 for name in _WORKLOAD_ORDER}
    while len(specs) < max_queries:
        added = False
        for workload in _WORKLOAD_ORDER:
            offset = offsets[workload]
            if offset >= len(candidates[workload]):
                continue
            spec, vector = candidates[workload][offset]
            offsets[workload] += 1
            specs.append(spec)
            vectors.append(vector)
            added = True
            if len(specs) >= max_queries:
                break
        if not added:
            break

    if not specs:
        raise RuntimeError("the memory contains no searchable entity/event/OCR/ASR text")
    return specs, np.asarray(vectors, dtype=np.float32)


def _artifact_size(path: str) -> int:
    if os.path.isfile(path) or os.path.islink(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(root, name)) for root, _dirs, files in os.walk(path) for name in files)


def _result_key(result: dict) -> tuple[str, str]:
    return str(result.get("macro_id", "")), str(result.get("node_id", ""))


def _parity(npz_results: list[list[dict]], zvec_results: list[list[dict]], top_k: int) -> dict:
    top1_matches = 0
    overlaps = []
    exact_orders = 0
    for npz_rows, zvec_rows in zip(npz_results, zvec_results, strict=True):
        left = [_result_key(row) for row in npz_rows[:top_k]]
        right = [_result_key(row) for row in zvec_rows[:top_k]]
        top1_matches += bool(left and right and left[0] == right[0])
        left_set, right_set = set(left), set(right)
        denominator = max(len(left_set), len(right_set), 1)
        overlaps.append(len(left_set & right_set) / denominator)
        exact_orders += left == right
    count = len(npz_results)
    return {
        "queries": count,
        "top1_agreement": top1_matches / count,
        f"top{top_k}_set_overlap": statistics.fmean(overlaps),
        f"top{top_k}_exact_order": exact_orders / count,
    }


def _percentiles(samples: list[float]) -> dict:
    values = np.asarray(samples, dtype=np.float64)
    return {
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
    }


def _close_index(index) -> None:
    if isinstance(index, ZvecEmbeddingIndex):
        index.close()


def _worker(index_path: str, query_pack: str, mode: str, top_k: int) -> dict:
    with np.load(query_pack, allow_pickle=False) as data:
        specs = json.loads(str(data["specs"]))
        vectors = np.asarray(data["vectors"], dtype=np.float32)

    load_started = time.perf_counter()
    index = load_retrieval_index(index_path)
    load_ms = (time.perf_counter() - load_started) * 1000
    try:
        if mode == "fts":
            index._dense_disabled = True

        def run_one(position: int) -> list[dict]:
            if mode == "hybrid":
                vector = vectors[position : position + 1]
                index._embed_batch = lambda _texts, value=vector: value
            spec = specs[position]
            return index.search(spec["query"], top_k=top_k, node_types=spec["node_types"])

        first_started = time.perf_counter()
        run_one(0)
        first_ms = (time.perf_counter() - first_started) * 1000

        warm_ms = []
        results = []
        for position in range(len(specs)):
            query_started = time.perf_counter()
            results.append(run_one(position))
            warm_ms.append((time.perf_counter() - query_started) * 1000)
        return {"load_ms": load_ms, "first_ms": first_ms, "warm_ms": warm_ms, "results": results}
    finally:
        _close_index(index)


def _run_worker_process(index_path: str, query_pack: str, mode: str, top_k: int) -> dict:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_worker",
        "--_index-path",
        index_path,
        "--_query-pack",
        query_pack,
        "--_mode",
        mode,
        "--top-k",
        str(top_k),
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode:
        raise RuntimeError(f"benchmark worker failed ({process.returncode}): {process.stderr or process.stdout}")
    return json.loads(process.stdout)


def _measure_runtime(index_path: str, query_pack: str, mode: str, top_k: int, runs: int) -> tuple[dict, list]:
    samples = [_run_worker_process(index_path, query_pack, mode, top_k) for _ in range(runs)]
    load = [sample["load_ms"] for sample in samples]
    first = [sample["first_ms"] for sample in samples]
    load_plus_first = [sample["load_ms"] + sample["first_ms"] for sample in samples]
    warm = [latency for sample in samples for latency in sample["warm_ms"]]
    return (
        {
            "load": _percentiles(load),
            "first_query": _percentiles(first),
            "load_plus_first_query": _percentiles(load_plus_first),
            "warm_query": _percentiles(warm),
        },
        samples[0]["results"],
    )


def run_benchmark(memory_dir: str, *, max_queries: int, runs: int, top_k: int) -> dict:
    source_path = detect_retrieval_index(memory_dir)
    if not source_path:
        raise RuntimeError(f"no embeddings.npz or embeddings.zvec found in {memory_dir}")
    nodes, embeddings = export_retrieval_index(source_path)
    specs, query_vectors = _build_query_pack(nodes, embeddings, max_queries)

    with tempfile.TemporaryDirectory(prefix="video-memory-backend-benchmark-") as temp_dir:
        query_pack = os.path.join(temp_dir, "queries.npz")
        np.savez(query_pack, specs=json.dumps(specs, ensure_ascii=False), vectors=query_vectors)

        index_paths = {}
        builds = {}
        for backend in ("npz", "zvec"):
            backend_dir = os.path.join(temp_dir, backend)
            build_started = time.perf_counter()
            index_path = save_retrieval_index(backend_dir, backend, nodes, embeddings)
            build_ms = (time.perf_counter() - build_started) * 1000
            index_paths[backend] = index_path
            builds[backend] = {"build_ms": build_ms, "size_bytes": _artifact_size(index_path)}

        backend_top_k = top_k * _TOOL_OVERFETCH
        runtime = {backend: {} for backend in index_paths}
        results = {backend: {} for backend in index_paths}
        for backend, index_path in index_paths.items():
            for mode in ("hybrid", "fts"):
                runtime[backend][mode], results[backend][mode] = _measure_runtime(
                    index_path, query_pack, mode, backend_top_k, runs
                )

        parity = {mode: _parity(results["npz"][mode], results["zvec"][mode], top_k) for mode in ("hybrid", "fts")}

    import zvec

    return {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "zvec": zvec.__version__,
        },
        "corpus": {
            "memory_dir": os.path.realpath(memory_dir),
            "source_index": source_path,
            "nodes": len(nodes),
            "dimension": int(embeddings.shape[1]),
            "queries": len(specs),
            "workloads": dict(collections.Counter(spec["workload"] for spec in specs)),
            "runs": runs,
            "top_k": top_k,
            "backend_top_k": backend_top_k,
        },
        "build": builds,
        "runtime": runtime,
        "parity": parity,
    }


def _format_ms(metric: dict) -> str:
    return f"{metric['p50_ms']:.2f}/{metric['p95_ms']:.2f}"


def _print_report(report: dict) -> None:
    corpus = report["corpus"]
    print(
        f"Corpus: {corpus['nodes']} nodes, {corpus['dimension']} dimensions, "
        f"{corpus['queries']} queries, {corpus['runs']} fresh-process runs"
    )
    print(f"Workloads: {json.dumps(corpus['workloads'], sort_keys=True)}")
    print(f"Top-K: tool={corpus['top_k']}, backend={corpus['backend_top_k']} (project overfetch)")
    print("\nBuild and storage")
    print("backend  build_ms  size_mib")
    for backend in ("npz", "zvec"):
        build = report["build"][backend]
        print(f"{backend:<7}  {build['build_ms']:>8.2f}  {build['size_bytes'] / (1024 * 1024):>8.2f}")

    print("\nRuntime p50/p95 milliseconds (fresh process per run)")
    print("backend  mode    load       first_query  load+first  warm_query")
    for backend in ("npz", "zvec"):
        for mode in ("hybrid", "fts"):
            metric = report["runtime"][backend][mode]
            print(
                f"{backend:<7}  {mode:<6}  {_format_ms(metric['load']):<10} "
                f"{_format_ms(metric['first_query']):<12} {_format_ms(metric['load_plus_first_query']):<11} "
                f"{_format_ms(metric['warm_query'])}"
            )

    print("\nResult parity")
    print("mode    top1_agreement  top-k_overlap  exact_order")
    overlap_key = f"top{corpus['top_k']}_set_overlap"
    order_key = f"top{corpus['top_k']}_exact_order"
    for mode in ("hybrid", "fts"):
        metric = report["parity"][mode]
        print(f"{mode:<6}  {metric['top1_agreement']:>14.2%}  {metric[overlap_key]:>13.2%}  {metric[order_key]:>11.2%}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("memory_dir", nargs="?", help="Existing video-memory directory")
    parser.add_argument("--queries", type=int, default=20, help="Maximum deterministic queries (default: 20)")
    parser.add_argument("--runs", type=int, default=5, help="Fresh-process repetitions per backend/mode (default: 5)")
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Tool-visible results; the backend receives twice this value (default: 10)",
    )
    parser.add_argument("--json-output", help="Optional path for the full JSON report")
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_index-path", help=argparse.SUPPRESS)
    parser.add_argument("--_query-pack", help=argparse.SUPPRESS)
    parser.add_argument("--_mode", choices=("hybrid", "fts"), help=argparse.SUPPRESS)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args._worker:
        if not args._index_path or not args._query_pack or not args._mode:
            parser.error("worker arguments are incomplete")
        print(json.dumps(_worker(args._index_path, args._query_pack, args._mode, args.top_k)))
        return 0
    if not args.memory_dir:
        parser.error("memory_dir is required")
    if args.queries <= 0 or args.runs <= 0 or args.top_k <= 0:
        parser.error("--queries, --runs, and --top-k must be positive")

    report = run_benchmark(args.memory_dir, max_queries=args.queries, runs=args.runs, top_k=args.top_k)
    _print_report(report)
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"\nJSON report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
