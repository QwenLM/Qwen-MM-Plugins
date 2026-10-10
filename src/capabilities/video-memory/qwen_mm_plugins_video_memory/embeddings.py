"""Text embeddings via the DashScope multimodal-embedding API."""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time

import numpy as np

try:  # server package imports shared; the flat build pipeline falls back to its env_config mirror
    from shared.env import get_env
except ImportError:
    from env_config import get_env

_MAX_RETRIES = 80
_RETRY_BASE_DELAY = 1.0


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


def _api_key() -> str:
    return get_env("DASHSCOPE_API_KEY", "")


def _embed_via_dashscope_native(texts: list[str], batch_size: int = 256, max_retries: int = _MAX_RETRIES) -> np.ndarray:
    """Embed texts via DashScope native multimodal-embedding API."""
    import requests as _requests

    api_key = _api_key()
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
