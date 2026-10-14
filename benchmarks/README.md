# Video-memory storage backend benchmark

Compare NPZ and zvec on the same stored nodes and float32 vectors, using hybrid RRF and the
FTS fallback with the project's entity/event, OCR and ASR filters. No API credentials are needed.

```bash
python benchmarks/video_memory_backend.py /path/to/video.mp4.memory --diagnose --json-output real.json
python benchmarks/video_memory_backend.py /path/to/video.mp4.memory --scale-nodes 1000 --diagnose --json-output scale-1000.json
python benchmarks/video_memory_backend.py /path/to/video.mp4.memory --scale-nodes 5000 --diagnose --json-output scale-5000.json
```

Install the project's `video-memory` extra first. Source artifacts are left untouched; the benchmark
builds both backends in temporary directories and removes those directories on completion.

## What the measurements mean

- **Load + first query** measures the benefit of persisted indexes on first memory access in a new
  process. NPZ loads vectors/metadata and rebuilds BM25; zvec imports its extra dependency and opens
  its persisted collection. Neither backend reloads the index on every query in a cached toolkit.
- **Warm query p50/p95** uses a loaded index after all queries have been warmed. Both backends retain
  their actual search implementation, including complete per-route rankings before RRF. Tool Top-10
  means backend Top-20, matching the toolkit's overfetch.
- **Build** includes artifact publication and read-back validation, plus any first backend import.
  NPZ writes arrays/JSON; zvec also constructs persistent vector, scalar and full-text indexes.
  **Storage** is logical retrieval-artifact bytes, not RAM usage or whole-memory-directory size.
- **Parity** compares IDs at the requested tool Top-K, before graph/cutoff postprocessing. Query text
  uses two rare tokens from a source node; its stored vector is reused as the query vector. This
  isolates backend behavior and is not a relevance or end-to-end quality evaluation.
- **Diagnostics** run separately after headline timings. They measure NPZ's BM25 rebuild, zvec's
  extra dependency initialization, build calls, query routes and Python result conversion. Conversion
  is included in route timings; do not add it again. Instrumented timings include wrapper overhead
  and are attribution evidence, not replacement latency numbers.

Fresh processes do not imply cold disks: OS file caches are retained. Shared Python/NumPy startup,
graph loading, MCP transport and remote query embedding are excluded. Default results use 20 queries
and five fresh-process repetitions per backend/mode; these are descriptive samples, not confidence
intervals. `--scale-nodes` repeats source text and slightly perturbs copied vectors with a fixed seed.
It tests a synthetic size trend, not a representative multi-video corpus, and disables parity output.

## Local observations, 2026-10-14

Apple M4 Pro, macOS 26.6.2, Python 3.12.13, NumPy 2.5.4, zvec 0.7.0. Source: the local
`aurora-31min.mp4.memory` smoke fixture, 81 nodes, 2560 dimensions. Its NPZ SHA-256 is
`f0c4993e3f282eb7dcb8305f2ea462f52fb893067cdb9e45315753065b43607f`.
The fixture is not distributed with this repository; use your own memory for reproducible local
comparisons. All three runs used the same 20 queries, five repetitions and tool Top-10.

**Persisted FTS eliminates repeated BM25 construction.** The separate diagnostic measured NPZ BM25
rebuilds of 0.90 / 5.06 / 28.26 ms at 81 / 1,000 / 5,000 nodes. zvec reopened the persisted collection
in 9.67 / 9.25 / 9.76 ms after its roughly 15 ms dependency import, without rebuilding BM25.

**At 5,000 synthetic nodes, first access was faster with zvec even including that import.** Median
load + first hybrid query was 50.63 ms for NPZ and 31.57 ms for zvec (38% lower); the FTS fallback was
40.47 vs 26.06 ms (36% lower). This is a first-access benefit, not a per-query saving.

The costs and current integration bottlenecks are:

- **Small-index initialization:** At 81 nodes, median load + first hybrid query was 1.47 vs 23.23 ms
  (NPZ vs zvec); at 1,000 nodes, 9.61 vs 26.18 ms. zvec's import/open cost exceeds the BM25 work saved
  at these sizes. The observed crossover is workload-specific, not a universal node threshold.
- **Warm hybrid retrieval:** At 5,000 nodes, median latency was 1.70 vs 5.06 ms. The current adapter
  asks both routes for up to the full collection size and returns all metadata, including text,
  before Python RRF. The diagnostic observed an average of 1,927 dense and 158 FTS candidates per
  query. Across 20 queries, SDK result conversion consumed 35.94 ms of 98.25 ms instrumented total;
  dense and FTS route calls, including conversion, consumed 73.85 and 10.86 ms. This demonstrates a
  material conversion cost; it does not attribute all remaining time to vector or BM25 computation.
  Warm FTS medians were close (0.43 vs 0.44 ms), with p95 of 0.59 vs 0.93 ms.
- **Build cost:** At 5,000 nodes, NPZ took 52.52 ms and zvec 957.31 ms. In a separate zvec diagnostic,
  collection creation took 277 ms, insertion 289 ms and flush 192 ms. Building persistent indexes
  shifts work to ingestion; initialization and ingestion also have their own costs. At 81 nodes,
  creation plus flush already took about 386 ms.
- **Disk footprint:** NPZ/zvec occupied 0.85/5.03 MiB at 81 nodes, 10.58/13.23 MiB at 1,000 nodes,
  and 52.92/57.54 MiB at 5,000 nodes. zvec persists additional indexes and database files. These
  measurements do not establish a RAM advantage.
- **Ordering:** On the real 81-node source, both modes had 100% Top-10 set overlap. Hybrid Top-1
  agreement was 100% and exact-order agreement 90%; FTS was 95% and 80%. Different tokenization/BM25
  behavior and ties need consideration; matching result sets does not imply identical ranking.

The first query optimization to investigate is retaining complete ID/score rankings while deferring
metadata/text fetches until after fusion, then checking parity and latency again. Simply bounding
each route's candidates would change the current exhaustive RRF semantics. Native fusion could also
reduce Python transfers, but requires separate verification of ranks, scores and tie behavior.
