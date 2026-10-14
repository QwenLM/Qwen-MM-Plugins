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
  extra dependency initialization, build calls, query routes, native query calls, RRF selection and
  Python result conversion. The native boundary includes result transfer through the binding, not
  just distance computation. Conversion
  is included in route timings; fetch conversion is included in the separately reported metadata
  fetch. Do not add these nested costs again. Instrumented timings include wrapper overhead and
  are attribution evidence, not replacement latency numbers.

Fresh processes do not imply cold disks: OS file caches are retained. Shared Python/NumPy startup,
graph loading, MCP transport and remote query embedding are excluded. Default results use 20 queries
and five fresh-process repetitions per backend/mode; these are descriptive samples, not confidence
intervals. `--scale-nodes` repeats source text and slightly perturbs copied vectors with a fixed seed.
It tests a synthetic size trend, not a representative multi-video corpus, and disables parity output.

## Exact fusion pruning and NumPy query vectors, 2026-10-14

The adapter now passes contiguous float32 NumPy query vectors directly to zvec, avoiding the previous
NumPy → Python list → NumPy conversion. It also avoids constructing full dense-rank/cosine maps and
sorting dense-only candidates below rank K. Such a candidate is strictly outranked by at least the
first K dense candidates, whose sparse contributions cannot be negative. All sparse matches remain
eligible, with ranks taken from the **complete** dense and FTS results. Relative insertion order and
the existing score arithmetic are preserved. Single-route searches use their already-sorted prefix.

With the same fixture and settings below, the new zvec warm hybrid p50 values are:

- 81 real nodes: 0.21 ms, previously 0.29 ms after deferred fetch.
- 1,000 synthetic nodes: 0.67 ms, previously 0.82 ms (18% lower).
- 5,000 synthetic nodes: 2.87 ms, previously 3.26 ms (12% lower), and 5.06 ms before either
  optimization (43% lower overall). Its p95 is 4.16 ms; the same rerun measured NPZ at 1.65/2.10 ms.
  Warm FTS is now 0.43/0.76 ms vs NPZ 0.42/0.57 ms. Load + first hybrid query still favors zvec:
  28.00 ms vs NPZ 48.15 ms. These are descriptive reruns, not confidence intervals or universal gains.

**The remaining bottleneck is full-ranking query execution and transfer, not RRF sorting.** In a
separate 20-query diagnostic at 5,000 nodes, zvec took 57.55 ms total: native query/binding calls
27.26 ms, candidate conversion 13.23 ms, RRF selection 1.62 ms, and metadata fetch 2.15 ms. Query
vector setup was only 0.07 ms. Other time includes SDK dispatch, list handling and instrumentation
overhead; do not interpret this as a pure native CPU profile. The engine still returns 41,686
ID/score candidates across these queries. NPZ instead computes over an already-loaded normalized
NumPy matrix and in-process BM25 structures, without constructing SDK documents for all candidates.

Before/after verification against `dcc9765` matched all 5,616 full-result comparisons described
below. A further 1,200 randomized ranking/K combinations matched exhaustive Python fusion exactly.
The existing NPZ/zvec parity metrics remain unchanged.

Native RRF is not a drop-in equivalent with this zvec 0.7.0 SDK: public multi-query `topk` also
controls each route's candidate count, and the native reranker replaces the original distance score.
Even after correcting the rank constant from 60 to 59 for its one-based formula, two disjoint
40-document rankings produced a different Top-20 order: the Python result starts `d0, s0, d1, s1`,
while native RRF starts `d0, s0, s1, d1`. This is a different tie policy, not an equivalent replacement.
Further reductions
in transfer therefore need separately controlled candidate/output limits and verified tie-breaking
and per-route score preservation. This implementation keeps exhaustive retrieval and does not use
private native query APIs in production.

## Deferred metadata fetching, 2026-10-14

Candidate queries now request only internal IDs and scores. Both complete rankings, dense-first
tie-breaking, RRF and cosine calculation are preserved. After fusion, one batch fetch retrieves the
selected records' fields without vectors, and results follow fused order rather than fetch order.
With the toolkit's overfetch, at most 20 records are fetched for a tool Top-10 request.

Compared with the baseline below, using the same corpus, queries and five repetitions:

- At 1,000 synthetic nodes, zvec warm hybrid p50 fell from 1.15 to 0.82 ms (29% lower).
- At 5,000 synthetic nodes, warm hybrid p50 fell from 5.06 to 3.26 ms (36% lower), and p95 from
  6.70 to 4.47 ms. The same rerun measured NPZ at 1.69/2.11 ms, so zvec is still slower for warm
  hybrid retrieval. zvec's load + first hybrid query remained lower: 31.93 vs 50.04 ms for NPZ.
- The extra fetch has a cost when little metadata can be avoided. Warm FTS p50 increased from
  0.44 to 0.53 ms at 5,000 nodes, and from 0.16 to 0.20 ms at 1,000 nodes. At the real 81-node
  size, hybrid changed from 0.26 to 0.29 ms and FTS from 0.07 to 0.08 ms.

The 5,000-node hybrid diagnostic still returned 41,686 ID/score candidates across 20 queries, but
only 400 records carried full fields, versus 41,686 previously. Candidate conversion decreased from
35.94 to 19.62 ms; the added metadata fetch took 2.76 ms, including 0.43 ms for fetched-record
conversion. The full-ranking query/transfer cost remains. In FTS mode, fetch took 1.92 ms across
20 queries (about 0.10 ms each), consistent with the observed latency increase.

An explicit before/after check loaded the implementation from commit `ea58530` and the new version
against the same read-only collections. All **5,616 comparisons matched exactly**, including result
order, metadata, RRF scores and cosine: 74 source queries plus four empty/stopword/unmatched/Unicode
cases, hybrid and FTS, route-specific/unfiltered/missing-type filters, Top-K 1/10/20/100, at
81/1,000/5,000 nodes. This checks optimization equivalence, including synthetic ties; it does not
claim semantic relevance. NPZ/zvec parity on the real source stayed unchanged from the baseline.

## Baseline before deferred metadata fetching, 2026-10-14

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

The baseline costs and integration bottlenecks were:

- **Small-index initialization:** At 81 nodes, median load + first hybrid query was 1.47 vs 23.23 ms
  (NPZ vs zvec); at 1,000 nodes, 9.61 vs 26.18 ms. zvec's import/open cost exceeded the BM25 work saved
  at these sizes. The observed crossover is workload-specific, not a universal node threshold.
- **Warm hybrid retrieval:** At 5,000 nodes, median latency was 1.70 vs 5.06 ms. The current adapter
  asked both routes for up to the full collection size and returned all metadata, including text,
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

Deferred metadata fetching above preserves exhaustive RRF. Simply bounding each route's candidates
would change those semantics. Native fusion could further reduce Python transfers, but requires
separate verification of ranks, scores and tie behavior.
