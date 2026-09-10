"""How text is matched and how rankings are fused — no memory, no model, no I/O.

Tokenising (CJK-aware), BM25 tuning constants, cosine similarity, reciprocal-rank fusion, and the
phonetic name matching that lets a mis-heard name still find its person.
"""

import difflib
import re

import numpy as np

from .omni_core import NAME_STOP, env_float

BM25_K1 = env_float("MEM_BM25_K1", 1.5)  # term-frequency saturation

BM25_B = env_float("MEM_BM25_B", 0.75)  # document-length normalisation

BM25_BOOST = env_float("MEM_BM25_BOOST", 2.0)  # weight on the entity-name field


def soundex(s):
    """Cheap phonetic code — matches a mis-heard STORED name to the query name (Lewis↔Louise)."""
    s = "".join(c for c in (s or "").upper() if c.isalpha())
    if not s:
        return ""
    codes = {
        **dict.fromkeys("BFPV", "1"),
        **dict.fromkeys("CGJKQSXZ", "2"),
        **dict.fromkeys("DT", "3"),
        "L": "4",
        **dict.fromkeys("MN", "5"),
        "R": "6",
    }
    out = s[0]
    prev = codes.get(s[0], "")
    for ch in s[1:]:
        cc = codes.get(ch, "")
        if cc and cc != prev:
            out += cc
        if ch not in "HW":
            prev = cc
    return (out + "000")[:4]


_Q_STOP = {
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "how",
    "is",
    "are",
    "does",
    "do",
    "did",
    "the",
    "based",
    "after",
    "before",
    "during",
    "robot",
    "cola",
    "juice",
    "orange",
    "coffee",
}


def query_person_names(q):
    """Person-name candidates in a question (Capitalized words minus stop / question words)."""
    return [
        t for t in re.findall(r"\b[A-Z][a-z]{2,}\b", q or "") if t.lower() not in NAME_STOP and t.lower() not in _Q_STOP
    ]


def phonetic_name_match(a, b):
    """True if two names sound alike (same soundex or high edit-similarity) — for QA recall of an
    entity whose stored name was mis-transcribed. Threshold 0.72 keeps distinct names apart
    (lily/emma=0, jack/robert=0) while catching mis-hears (lewis/louise, cora/cara, isha/aisha)."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return soundex(a) == soundex(b) or difflib.SequenceMatcher(None, a, b).ratio() >= 0.72


# ============================================================ RETRIEVAL HELPERS
def toks(s):
    return set(re.findall(r"[a-z0-9']+|[一-鿿]", str(s or "").lower()))


_STOP = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "of",
    "to",
    "in",
    "on",
    "at",
    "and",
    "or",
    "do",
    "does",
    "did",
    "you",
    "i",
    "he",
    "she",
    "it",
    "they",
    "we",
    "this",
    "that",
    "what",
    "who",
    "which",
    "how",
    "when",
    "where",
    "why",
    "please",
    "hmm",
    "um",
    "uh",
    "的",
    "了",
    "是",
    "在",
    "吗",
    "呢",
    "我",
    "你",
    "他",
    "她",
    "它",
}


def kw(s):
    return {t for t in toks(s) if t not in _STOP}


def tok_list(s):
    """Tokens WITH their repeats — BM25 scores on term frequency, which toks() discards."""
    return [t for t in re.findall(r"[a-z0-9']+|[一-鿿]", str(s or "").lower()) if t not in _STOP]


def cosine(a, b):
    a = np.asarray(a, dtype="float32")
    b = np.asarray(b, dtype="float32")
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def rrf(rank_lists, rrf_k=60):
    score = {}
    for lst in rank_lists:
        for rank, iid in enumerate(lst):
            score[iid] = score.get(iid, 0.0) + 1.0 / (rrf_k + rank + 1)
    return score
