"""Text embeddings via the DashScope multimodal-embedding API."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import time

import numpy as np

try:  # server package imports shared; the flat build pipeline falls back to its env_config mirror
    from shared.env import get_env
except ImportError:
    from env_config import get_env

_MAX_RETRIES = 80
_RETRY_BASE_DELAY = 1.0

NPZ_INDEX_FILENAME = "embeddings.npz"
ZVEC_INDEX_DIRNAME = "embeddings.zvec"
RETRIEVAL_BACKENDS = ("npz", "zvec")


# DashScope native multimodal-embedding endpoint.
def _dashscope_native_host() -> str:
    """Native-REST host, derived from DASHSCOPE_BASE_URL so intl/proxy endpoints work too."""
    base = (get_env("DASHSCOPE_BASE_URL") or "").strip()
    if base:
        from urllib.parse import urlsplit

        p = urlsplit(base)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    return "https://dashscope.aliyuncs.com"


DASHSCOPE_NATIVE_MODEL = "qwen3-vl-embedding"
DASHSCOPE_NATIVE_DIM = 2560
# Native endpoint rejects large batches; cap per-request size.
_NATIVE_MAX_BATCH = 10


def _embed_via_dashscope_native(texts: list[str], batch_size: int = 256, max_retries: int = _MAX_RETRIES) -> np.ndarray:
    """Embed texts via DashScope native multimodal-embedding API."""
    import requests as _requests

    api_key = get_env("DASHSCOPE_API_KEY", "")
    url = f"{_dashscope_native_host()}/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
    batch_size = min(batch_size, _NATIVE_MAX_BATCH)
    all_embs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        contents = [{"text": t} for t in batch]
        payload = {
            "model": DASHSCOPE_NATIVE_MODEL,
            "input": {"contents": contents},
            "parameters": {"dimension": DASHSCOPE_NATIVE_DIM},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        resp = _requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code == 429:
            for attempt in range(max_retries):
                # Exponential backoff (capped).
                delay = min(_RETRY_BASE_DELAY * (2 ** min(attempt, 6)) + random.random(), 60.0)
                print(
                    f"  [embed-native] 429 rate limit (batch {i // batch_size + 1}, attempt {attempt + 1}/{max_retries}), retrying in {delay:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                resp = _requests.post(url, headers=headers, json=payload, timeout=120)
                if resp.status_code != 429:
                    break
            else:
                raise RuntimeError(
                    f"DashScope native embedding API failed after {max_retries} retries (429 rate limit)"
                )
        resp.raise_for_status()
        data = resp.json()
        embs = [e["embedding"] for e in data["output"]["embeddings"]]
        all_embs.extend(embs)
    return np.array(all_embs, dtype=np.float32)


# Hint shown when the query-time embedding backend is missing/mismatched.
_EMBED_BACKEND_HINT = "Fix: set DASHSCOPE_API_KEY for the DashScope multimodal-embedding API."


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "from",
        "by",
        "as",
        "into",
        "about",
        "and",
        "or",
        "but",
        "not",
        "no",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "we",
        "i",
        "you",
        "me",
        "him",
        "her",
        "us",
        "的",
        "了",
        "在",
        "是",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
    }
)

_TOKEN_RE = re.compile(r"[\w一-鿿]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase tokens, filtering stopwords."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class EmbeddingIndex:
    def __init__(self):
        self.nodes: list[dict] = []
        self.embeddings: np.ndarray | None = None
        self._normed: np.ndarray | None = None
        self._inv_index: dict[str, list[tuple[int, int, float]]] = {}
        self._doc_lens: list[int] = []
        self._avg_dl: float = 0.0
        self._dense_disabled: bool = False

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        return _embed_via_dashscope_native(texts)

    def _set_embeddings(self, embeddings: np.ndarray | None):
        self.embeddings = embeddings
        self._normed = None

    def _build_sparse_index(self):
        """Build BM25 inverted index from node texts."""
        n = len(self.nodes)
        if n == 0:
            return
        doc_tokens: list[list[str]] = []
        df: dict[str, int] = {}
        for node in self.nodes:
            tokens = _tokenize(node.get("text", ""))
            doc_tokens.append(tokens)
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1

        self._doc_lens = [len(dt) for dt in doc_tokens]
        self._avg_dl = sum(self._doc_lens) / n if n > 0 else 1.0

        self._inv_index = {}
        for i, tokens in enumerate(doc_tokens):
            tf_map: dict[str, int] = {}
            for t in tokens:
                tf_map[t] = tf_map.get(t, 0) + 1
            for t, tf in tf_map.items():
                idf = math.log((n - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
                self._inv_index.setdefault(t, []).append((i, tf, idf))

    def _sparse_search(self, query: str) -> dict[int, float]:
        """BM25 scoring for query against all nodes. Returns {node_idx: score}."""
        tokens = _tokenize(query)
        if not tokens or not self._inv_index:
            return {}
        k1 = 1.2
        b = 0.75
        scores: dict[int, float] = {}
        for t in tokens:
            postings = self._inv_index.get(t)
            if not postings:
                continue
            for idx, tf, idf in postings:
                dl = self._doc_lens[idx]
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / self._avg_dl))
                scores[idx] = scores.get(idx, 0.0) + idf * tf_norm
        return scores

    def build(self, nodes: list[dict], batch_size: int = 256, max_workers: int = 4):
        """Build embedding index from a node list with parallel API calls."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        self.nodes = nodes
        batches = []
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]
            batches.append((i, [n["text"] for n in batch]))

        n_workers = min(max_workers, len(batches))
        results = [None] * len(batches)
        done_count = 0

        def _embed_one(batch_idx, texts):
            return batch_idx, self._embed_batch(texts)

        if n_workers <= 1:
            for batch_idx, texts in batches:
                _, embs = _embed_one(batch_idx, texts)
                results[batch_idx // batch_size] = embs
                done_count = batch_idx + len(texts)
                print(f"  Embedded {min(done_count, len(nodes))}/{len(nodes)} nodes", file=sys.stderr)
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_embed_one, i, texts): idx for idx, (i, texts) in enumerate(batches)}
                for future in as_completed(futures):
                    slot = futures[future]
                    _, embs = future.result()
                    results[slot] = embs
                    done_count += len(batches[slot][1])
                    print(f"  Embedded {done_count}/{len(nodes)} nodes ({n_workers} workers)", file=sys.stderr)

        self._set_embeddings(np.vstack(results).astype(np.float32))
        self._build_sparse_index()

    def check_dimension_compatibility(self):
        """Raise RuntimeError if query-embedding dimension mismatches stored embeddings."""
        if self.embeddings is None or len(self.nodes) == 0:
            return
        stored_dim = self.embeddings.shape[1]
        try:
            # Cap retries here so a rate-limited endpoint doesn't stall every toolkit load for
            # minutes before BM25 fallback; the full retry budget still applies to real queries.
            q_emb = _embed_via_dashscope_native(["dimension check"], max_retries=3)
            query_dim = q_emb.shape[1]
        except Exception as e:
            raise RuntimeError(
                f"\n{'=' * 60}\n"
                f"EMBEDDING BACKEND UNAVAILABLE\n"
                f"  Stored embeddings: {stored_dim}-dim\n"
                f"  Error: {e}\n\n"
                f"search_nodes cannot work without a compatible embedding backend.\n\n"
                f"{_EMBED_BACKEND_HINT}\n"
                f"{'=' * 60}"
            ) from e
        if query_dim != stored_dim:
            raise RuntimeError(
                f"\n{'=' * 60}\n"
                f"EMBEDDING DIMENSION MISMATCH\n"
                f"  Stored embeddings: {stored_dim}-dim\n"
                f"  Query embeddings:  {query_dim}-dim\n\n"
                f"search_nodes will produce wrong results — the stored embeddings were built\n"
                f"with a different model than the current query backend.\n\n"
                f"{_EMBED_BACKEND_HINT}\n"
                f"{'=' * 60}"
            )
        print(f"[embed] Dimension check OK: stored={stored_dim}, query={query_dim}", file=sys.stderr)

    def _normalized(self) -> np.ndarray:
        """Return L2-normalized embedding matrix (cached)."""
        if self._normed is None or self._normed.shape[0] != self.embeddings.shape[0]:
            norms = np.linalg.norm(self.embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            self._normed = self.embeddings / norms
        return self._normed

    def search(
        self,
        query: str,
        top_k: int = 10,
        node_types: list[str] | None = None,
    ) -> list[dict]:
        """Hybrid search: dense cosine + sparse BM25, fused with RRF."""
        if self.embeddings is None or len(self.nodes) == 0:
            return []

        indices = list(range(len(self.nodes)))
        if node_types:
            nt_lower = {t.lower() for t in node_types}
            indices = [i for i in indices if self.nodes[i].get("node_type", "").lower() in nt_lower]
        if not indices:
            return []

        q_emb = None
        if not self._dense_disabled:
            try:
                cand = self._embed_batch([query])[0].astype(np.float32)
                if cand.shape[0] != self.embeddings.shape[1]:
                    print(
                        f"[embed] dimension mismatch (stored={self.embeddings.shape[1]}, "
                        f"query={cand.shape[0]}); falling back to BM25-only search",
                        file=sys.stderr,
                    )
                    self._dense_disabled = True
                else:
                    q_emb = cand
            except Exception as e:
                print(f"[embed] dense backend failed ({e}); falling back to BM25-only search", file=sys.stderr)
                self._dense_disabled = True

        if q_emb is not None:
            normed = self._normalized()
            q_norm = q_emb / (np.linalg.norm(q_emb) or 1)
            cosine_scores = normed @ q_norm
            dense_ranked = sorted(
                [(i, float(cosine_scores[i])) for i in indices],
                key=lambda x: x[1],
                reverse=True,
            )
            dense_rank = {idx: rank for rank, (idx, _) in enumerate(dense_ranked)}
        else:
            cosine_scores = None
            dense_rank = {}

        sparse_scores = self._sparse_search(query)
        sparse_ranked = sorted(
            [(i, sparse_scores[i]) for i in indices if sparse_scores.get(i, 0.0) > 0],
            key=lambda x: x[1],
            reverse=True,
        )
        sparse_rank = {idx: rank for rank, (idx, _) in enumerate(sparse_ranked)}

        if not dense_rank and not sparse_rank:
            return []

        rrf_k = 60
        fused = []
        for i in indices:
            rrf_score = 0.0
            if i in sparse_rank:
                rrf_score += 1.0 / (rrf_k + sparse_rank[i])
            if i in dense_rank:
                rrf_score += 1.0 / (rrf_k + dense_rank[i])
            if rrf_score == 0:
                continue
            fused.append((i, rrf_score, float(cosine_scores[i]) if cosine_scores is not None else 0.0))
        fused.sort(key=lambda x: x[1], reverse=True)

        results = []
        for i, rrf_score, cosine in fused[:top_k]:
            node = dict(self.nodes[i])
            node["score"] = round(rrf_score, 6)
            node["cosine"] = round(cosine, 4)
            results.append(node)
        return results

    def save(self, path: str):
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            np.savez(
                f,
                embeddings=self.embeddings,
                nodes=json.dumps(self.nodes, ensure_ascii=False),
            )
        os.replace(tmp, path)

    def load(self, path: str):
        with np.load(path, allow_pickle=False) as data:
            self._set_embeddings(data["embeddings"])
            self.nodes = json.loads(str(data["nodes"]))
        self._build_sparse_index()


def retrieval_index_path(memory_dir: str, backend: str) -> str:
    """Return the on-disk artifact path for a retrieval backend."""
    if backend == "npz":
        return os.path.join(memory_dir, NPZ_INDEX_FILENAME)
    if backend == "zvec":
        return os.path.join(memory_dir, ZVEC_INDEX_DIRNAME)
    raise ValueError(f"unsupported retrieval backend: {backend!r}; expected one of {RETRIEVAL_BACKENDS}")


def detect_retrieval_index(memory_dir: str, configured: str | None = None) -> str:
    """Resolve the one retrieval artifact in ``memory_dir``.

    An explicit existing path wins. Auto-detection otherwise rejects a directory that contains
    both backends: without a manifest there is no reliable way to know which artifact matches the
    current graph.
    """
    if configured:
        configured = os.path.realpath(configured)
        if os.path.exists(configured):
            return configured
    candidates = [
        os.path.realpath(retrieval_index_path(memory_dir, backend))
        for backend in ("zvec", "npz")
        if os.path.exists(retrieval_index_path(memory_dir, backend))
    ]
    if len(candidates) > 1:
        raise RuntimeError(
            f"Memory directory contains both {ZVEC_INDEX_DIRNAME}/ and {NPZ_INDEX_FILENAME}: {memory_dir}. "
            "A memory must contain exactly one retrieval artifact; remove the inactive backend."
        )
    if candidates:
        return candidates[0]
    return os.path.realpath(configured) if configured else ""


def _require_zvec():
    try:
        import zvec
    except ImportError as e:
        raise RuntimeError(
            "The zvec retrieval backend requires zvec>=0.7,<1. "
            "Install the video-memory extra or run `pip install 'zvec>=0.7,<1'`."
        ) from e
    try:
        version = tuple(map(int, zvec.__version__.split(".")[:2]))
    except (AttributeError, ValueError) as e:
        raise RuntimeError("Unable to determine the installed zvec version; expected zvec>=0.7,<1.") from e
    if not (0, 7) <= version < (1, 0):
        raise RuntimeError(f"Unsupported zvec version {zvec.__version__}; expected zvec>=0.7,<1.")
    return zvec


def _zvec_doc_id(node: dict, position: int) -> str:
    """Build a valid internal zvec primary key without changing the public node_id."""
    raw = f"{node.get('macro_id', '')}\0{node.get('node_id', '')}\0{position}".encode()
    return f"node_{position:08d}_{hashlib.sha256(raw).hexdigest()[:16]}"


def save_zvec_index(path: str, nodes: list[dict], embeddings: np.ndarray) -> None:
    """Atomically create a persistent zvec collection from precomputed embeddings."""
    zvec = _require_zvec()
    embeddings = np.asarray(embeddings, dtype=np.float32)
    if embeddings.ndim != 2 or embeddings.shape[0] != len(nodes) or embeddings.shape[1] == 0:
        raise ValueError("embeddings must be a non-empty 2-D matrix aligned with nodes")

    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    stage_root = tempfile.mkdtemp(prefix=".embeddings-zvec-", dir=parent)
    stage_path = os.path.join(stage_root, "collection")
    backup_path = os.path.join(stage_root, "previous")
    collection = None
    old_moved = False
    cleanup_stage = True
    try:
        schema = zvec.CollectionSchema(
            name="video_memory",
            fields=[
                zvec.FieldSchema("node_id", zvec.DataType.STRING),
                zvec.FieldSchema("node_type", zvec.DataType.STRING, index_param=zvec.InvertIndexParam()),
                zvec.FieldSchema("macro_id", zvec.DataType.STRING, index_param=zvec.InvertIndexParam()),
                zvec.FieldSchema(
                    "text",
                    zvec.DataType.STRING,
                    index_param=zvec.FtsIndexParam(tokenizer_name="standard", filters=["lowercase"]),
                ),
            ],
            vectors=zvec.VectorSchema(
                "embedding",
                zvec.DataType.VECTOR_FP32,
                int(embeddings.shape[1]),
                index_param=zvec.FlatIndexParam(metric_type=zvec.MetricType.COSINE),
            ),
        )
        collection = zvec.create_and_open(stage_path, schema)
        for start in range(0, len(nodes), 1000):
            docs = []
            for position in range(start, min(start + 1000, len(nodes))):
                node = nodes[position]
                docs.append(
                    zvec.Doc(
                        id=_zvec_doc_id(node, position),
                        fields={
                            "node_id": str(node.get("node_id", "")),
                            "node_type": str(node.get("node_type", "")),
                            "macro_id": str(node.get("macro_id", "")),
                            "text": str(node.get("text", "")),
                        },
                        vectors={"embedding": embeddings[position].tolist()},
                    )
                )
            collection.insert(docs)
        collection.flush()
        collection.close()
        collection = None

        if os.path.lexists(path):
            os.replace(path, backup_path)
            old_moved = True
        try:
            os.replace(stage_path, path)
        except Exception:
            if old_moved and os.path.lexists(backup_path):
                try:
                    os.replace(backup_path, path)
                except Exception as rollback_error:
                    cleanup_stage = False
                    raise RuntimeError(
                        f"failed to publish zvec collection and restore the previous collection; "
                        f"the previous collection is preserved at {backup_path}"
                    ) from rollback_error
            raise
        if old_moved:
            shutil.rmtree(backup_path, ignore_errors=True)
    finally:
        if collection is not None:
            collection.close()
        if cleanup_stage:
            shutil.rmtree(stage_root, ignore_errors=True)


class ZvecEmbeddingIndex:
    """Persistent vector + full-text retrieval over a zvec collection."""

    _OUTPUT_FIELDS = ["node_id", "node_type", "macro_id", "text"]

    def __init__(self):
        self._collection = None
        self._dimension = 0
        self._doc_count = 0
        self._dense_disabled = False

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        return _embed_via_dashscope_native(texts)

    def load(self, path: str) -> None:
        zvec = _require_zvec()
        self.close()
        try:
            self._collection = zvec.open(path, zvec.CollectionOption(read_only=True))
            schema = self._collection.schema
            vector = schema.vector("embedding")
            if vector is None:
                raise RuntimeError(f"zvec collection has no 'embedding' vector field: {path}")
            missing_fields = [name for name in self._OUTPUT_FIELDS if schema.field(name) is None]
            if missing_fields:
                raise RuntimeError(f"zvec collection is missing required fields {missing_fields}: {path}")
            self._dimension = int(vector.dimension)
            self._doc_count = int(self._collection.stats.doc_count)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._collection is not None:
            self._collection.close()
            self._collection = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def check_dimension_compatibility(self) -> None:
        if self._collection is None or self._doc_count == 0:
            return
        try:
            q_emb = _embed_via_dashscope_native(["dimension check"], max_retries=3)
            query_dim = q_emb.shape[1]
        except Exception as e:
            raise RuntimeError(
                f"Stored embeddings are {self._dimension}-dim, but the query embedding backend is unavailable. "
                f"{_EMBED_BACKEND_HINT} Error: {e}"
            ) from e
        if query_dim != self._dimension:
            raise RuntimeError(
                f"Embedding dimension mismatch: stored={self._dimension}, query={query_dim}. {_EMBED_BACKEND_HINT}"
            )

    @staticmethod
    def _node_type_filter(node_types: list[str] | None) -> str | None:
        if not node_types:
            return None
        values = sorted({str(value).lower() for value in node_types})
        quoted = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
        return f"node_type IN ({quoted})"

    def _query(self, query, *, filter_expr: str | None):
        if self._collection is None:
            return []
        return self._collection.query(
            query,
            topk=max(1, self._doc_count),
            filter=filter_expr,
            output_fields=[],
            include_vector=False,
        )

    @staticmethod
    def _select_fused(dense_docs, sparse_docs, top_k: int) -> list[tuple[str, float, float]]:
        """Select exact RRF Top-K without sorting candidates that cannot reach it."""
        rrf_k = 60
        if not dense_docs:
            return [(doc.id, 1.0 / (rrf_k + rank), 0.0) for rank, doc in enumerate(sparse_docs[:top_k])]
        if not sparse_docs:
            return [
                (doc.id, 1.0 / (rrf_k + rank), 1.0 - float(doc.score)) for rank, doc in enumerate(dense_docs[:top_k])
            ]

        sparse_rank = {doc.id: rank for rank, doc in enumerate(sparse_docs)}
        fused = []
        for rank, doc in enumerate(dense_docs):
            sparse_position = sparse_rank.pop(doc.id, None)
            # A dense-only item below rank K is strictly outranked by the first K
            # dense items, even after their nonnegative sparse contributions.
            # Keep EVERY sparse match, using its rank in the complete dense list.
            if rank < top_k or sparse_position is not None:
                score = 0.0 if sparse_position is None else 1.0 / (rrf_k + sparse_position)
                score += 1.0 / (rrf_k + rank)
                fused.append((doc.id, score, 1.0 - float(doc.score)))
        # Unmatched sparse entries retain their original insertion order, after
        # dense entries, preserving the existing stable tie-breaking.
        fused.extend((doc_id, 1.0 / (rrf_k + rank), 0.0) for doc_id, rank in sparse_rank.items())
        fused.sort(key=lambda item: item[1], reverse=True)
        return fused[:top_k]

    def search(
        self,
        query: str,
        top_k: int = 10,
        node_types: list[str] | None = None,
    ) -> list[dict]:
        """Run exact cosine and persisted FTS routes, then preserve the existing RRF behavior."""
        if self._collection is None or self._doc_count == 0 or top_k <= 0:
            return []
        zvec = _require_zvec()
        filter_expr = self._node_type_filter(node_types)

        dense_docs = []
        if not self._dense_disabled:
            try:
                q_emb = np.ascontiguousarray(self._embed_batch([query])[0], dtype=np.float32)
                if q_emb.shape[0] != self._dimension:
                    print(
                        f"[embed] dimension mismatch (stored={self._dimension}, query={q_emb.shape[0]}); "
                        "falling back to FTS-only search",
                        file=sys.stderr,
                    )
                    self._dense_disabled = True
                else:
                    dense_docs = self._query(
                        zvec.Query(field_name="embedding", vector=q_emb),
                        filter_expr=filter_expr,
                    )
            except Exception as e:
                print(f"[embed] dense backend failed ({e}); falling back to FTS-only search", file=sys.stderr)
                self._dense_disabled = True

        sparse_docs = []
        sparse_query = " ".join(_tokenize(query))
        if sparse_query:
            try:
                sparse_docs = self._query(
                    zvec.Query(field_name="text", fts=zvec.Fts(match_string=sparse_query)),
                    filter_expr=filter_expr,
                )
            except Exception as e:
                print(f"[fts] zvec full-text search failed ({e})", file=sys.stderr)

        selected = self._select_fused(dense_docs, sparse_docs, top_k)
        if not selected:
            return []

        docs = self._collection.fetch(
            [doc_id for doc_id, _, _ in selected],
            output_fields=self._OUTPUT_FIELDS,
            include_vector=False,
        )
        results = []
        for doc_id, score, cosine in selected:
            if doc_id not in docs:
                raise RuntimeError(f"zvec result is missing during metadata fetch: {doc_id}")
            doc = docs[doc_id]
            node = {name: doc.fields.get(name, "") for name in self._OUTPUT_FIELDS}
            node["score"] = round(score, 6)
            node["cosine"] = round(cosine, 4)
            results.append(node)
        return results

    def export_records(self) -> tuple[list[dict], np.ndarray]:
        """Read all records for directory-level memory merging without re-embedding."""
        if self._collection is None:
            return [], np.zeros((0, 0), dtype=np.float32)
        nodes = []
        vectors = []
        with self._collection.iter_docs(output_fields=self._OUTPUT_FIELDS, include_vector=True) as docs:
            for doc in docs:
                nodes.append({name: doc.fields.get(name, "") for name in self._OUTPUT_FIELDS})
                vectors.append(doc.vectors["embedding"])
        if not vectors:
            return nodes, np.zeros((0, self._dimension), dtype=np.float32)
        return nodes, np.asarray(vectors, dtype=np.float32)


def load_retrieval_index(path: str):
    """Load either a zvec directory or a legacy NPZ retrieval index."""
    if os.path.isdir(path):
        index = ZvecEmbeddingIndex()
    else:
        index = EmbeddingIndex()
    index.load(path)
    return index


def export_retrieval_index(path: str) -> tuple[list[dict], np.ndarray]:
    """Return node metadata and vectors from either supported retrieval artifact."""
    index = load_retrieval_index(path)
    try:
        if isinstance(index, ZvecEmbeddingIndex):
            return index.export_records()
        return [dict(node) for node in index.nodes], np.asarray(index.embeddings, dtype=np.float32)
    finally:
        if isinstance(index, ZvecEmbeddingIndex):
            index.close()


def _validate_retrieval_index(
    path: str,
    expected_count: int | None = None,
    expected_dimension: int | None = None,
) -> None:
    """Reopen a freshly-written artifact before making it the only active backend."""
    index = load_retrieval_index(path)
    try:
        if isinstance(index, ZvecEmbeddingIndex):
            actual_count = index._doc_count
            actual_dimension = index._dimension
        else:
            actual_count = len(index.nodes)
            actual_dimension = index.embeddings.shape[1] if index.embeddings is not None else 0
        if (expected_count is not None and actual_count != expected_count) or (
            expected_dimension is not None and actual_dimension != expected_dimension
        ):
            raise RuntimeError(
                f"retrieval index validation failed for {path}: "
                f"expected ({expected_count}, {expected_dimension}), "
                f"got ({actual_count}, {actual_dimension})"
            )
    finally:
        if isinstance(index, ZvecEmbeddingIndex):
            index.close()


def _remove_retrieval_artifact(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def activate_retrieval_backend(memory_dir: str, backend: str) -> str:
    """Validate ``backend`` and remove the inactive retrieval artifact.

    This enforces the storage invariant that one memory directory contains exactly one retrieval
    backend. Cleanup happens only after the selected artifact has been reopened successfully.
    """
    active_path = os.path.abspath(retrieval_index_path(memory_dir, backend))
    _validate_retrieval_index(active_path)

    inactive_backend = "npz" if backend == "zvec" else "zvec"
    inactive_path = os.path.abspath(retrieval_index_path(memory_dir, inactive_backend))
    _remove_retrieval_artifact(inactive_path)
    return os.path.realpath(active_path)


def save_retrieval_index(
    memory_dir: str,
    backend: str,
    nodes: list[dict],
    embeddings: np.ndarray,
) -> str:
    """Stage, validate, and publish exactly one retrieval backend for a memory."""
    embeddings = np.asarray(embeddings, dtype=np.float32)
    if embeddings.ndim != 2 or embeddings.shape[0] != len(nodes) or embeddings.shape[1] == 0:
        raise ValueError("embeddings must be a non-empty 2-D matrix aligned with nodes")

    if backend not in RETRIEVAL_BACKENDS:
        raise ValueError(f"unsupported retrieval backend: {backend!r}; expected one of {RETRIEVAL_BACKENDS}")

    os.makedirs(memory_dir, exist_ok=True)
    path = retrieval_index_path(memory_dir, backend)
    inactive_backend = "npz" if backend == "zvec" else "zvec"
    inactive_path = retrieval_index_path(memory_dir, inactive_backend)
    stage_root = tempfile.mkdtemp(prefix=".retrieval-index-", dir=memory_dir)
    stage_path = os.path.join(stage_root, os.path.basename(path))
    active_backup = os.path.join(stage_root, "previous-active")
    inactive_backup = os.path.join(stage_root, "previous-inactive")
    active_moved = False
    inactive_moved = False
    cleanup_stage = True

    try:
        if backend == "zvec":
            save_zvec_index(stage_path, nodes, embeddings)
        else:
            index = EmbeddingIndex()
            index.nodes = nodes
            index._set_embeddings(embeddings)
            index.save(stage_path)

        _validate_retrieval_index(stage_path, len(nodes), int(embeddings.shape[1]))

        try:
            if os.path.lexists(path):
                os.replace(path, active_backup)
                active_moved = True
            if os.path.lexists(inactive_path):
                os.replace(inactive_path, inactive_backup)
                inactive_moved = True
            os.replace(stage_path, path)
        except Exception:
            rollback_errors = []
            for moved, backup, destination in (
                (inactive_moved, inactive_backup, inactive_path),
                (active_moved, active_backup, path),
            ):
                if moved and os.path.lexists(backup):
                    try:
                        os.replace(backup, destination)
                    except Exception as rollback_error:
                        rollback_errors.append(rollback_error)
            if rollback_errors:
                cleanup_stage = False
                raise RuntimeError(
                    f"failed to publish retrieval index and fully restore the previous artifacts; "
                    f"recoverable backups are preserved at {stage_root}"
                ) from rollback_errors[0]
            raise
    finally:
        if cleanup_stage:
            shutil.rmtree(stage_root, ignore_errors=True)

    return path
