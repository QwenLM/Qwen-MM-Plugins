"""What the build path and the query path both need, and nothing else.

Config-derived endpoints and credentials, the omni and embedding clients, throttling/stall handling,
the vector codec, ffmpeg duration probing, the name-alignment stopword table, and StoreBase — the
memory containers plus the serialization both sides have to agree on.

TWO COPIES OF THIS FILE EXIST AND MUST STAY IDENTICAL:
  qwen_mm_plugins_omni_memory/omni_core.py          (the MCP server reads memories with it)
  skill/script/build_memory/omni_core.py            (the build script writes them with it)

Neither copy can import the other, in either direction. Several harnesses install a skill by copying
skill/ alone, so the build script cannot count on the server package being anywhere near it; and the
wheel that installs the server does not ship skill/, so the package cannot reach the build script.
That is why this is a copy rather than a shared module, and it is the same trade video-memory makes
with schema.py and embeddings.py.

The copies are pinned by tests/test_omni_memory.py::test_the_two_copies_of_omni_core_stay_identical,
which compares every line below the WIRING marker. Edit one copy and that test fails until the other
matches — the format two programs write and read must never drift silently.
"""

import base64
import os
import random
import re
import subprocess
import sys
import threading
import time

import env_config as config  # WIRING — the server copy reads `from . import config` on this line
import numpy as np
from openai import OpenAI


# ══════════════════ IDENTICAL IN BOTH COPIES BELOW THIS LINE ══════════════════
def diag(*args, **kwargs):
    """Progress and diagnostics, always on stderr — never stdout.

    The MCP server speaks JSON-RPC over stdout, where one stray print corrupts the framing and drops
    the connection. The build path logs through here too, so `2>&1` captures a whole build in one file.
    """
    kwargs.setdefault("file", sys.stderr)
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


BASE_URL, MODEL, _CHAT_KEY = config.chat_config()

EMBED_BASE_URL, EMBED_MODEL, _EMBED_KEY = config.embed_config()

RESIDENT_ENTITY_CAP = 40  # over cap, people with low frequency×recency are evicted


def env_float(name, default=None):
    """Uncatalogued MEM_* float knob; `default` when unset, blank or unparseable."""
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def env_int(name, default):
    """Uncatalogued MEM_* int knob; `default` when unset, blank or unparseable."""
    try:
        v = os.environ.get(name, "").strip()
        return int(v) if v else default
    except ValueError:
        return default


# Temperature for every chat/omni call — extraction, stage-2, PLAN, answer, replay. 0 keeps building
# and answering deterministic; MEM_TEMPERATURE overrides.
GEN_TEMPERATURE = env_float("MEM_TEMPERATURE", 0.0)

TEMP_KWARGS = {"temperature": GEN_TEMPERATURE} if GEN_TEMPERATURE is not None else {}


def get_client():
    """Client for the CHAT / omni model (endpoint per config.chat_config())."""
    return OpenAI(api_key=_CHAT_KEY or "EMPTY", base_url=BASE_URL)


def set_chat_model(name):
    """Point every omni call in this process at `name`.

    Extraction reads MODEL directly while semantic induction and name alignment take a model
    argument, so the build sets the name here once instead of threading it through three signatures.
    """
    global MODEL
    if name:
        MODEL = name
    return MODEL


def get_embed_client():
    """Client for EMBEDDINGS, or None when no credential is configured for them.

    Embeddings do not follow the chat endpoint — a self-hosted omni server has no embedding model.
    None rather than a client holding a placeholder key, which would spend two requests that can only
    401 plus a 2s backoff on every query before falling back to BM25.
    """
    if not _EMBED_KEY:
        return None
    return OpenAI(api_key=_EMBED_KEY, base_url=EMBED_BASE_URL)


def normalize_acoustic_events(events):
    """Normalize acoustic.events to unified structured format.
    Old format: ["background music", "glass clinking"]
    New format: [{"event": "background music", "start_sec": 45.0, "end_sec": 70.0, "continues_from_previous": true}]
    Returns list of dicts always."""
    if not events:
        return []
    out = []
    for e in events:
        if isinstance(e, str):
            out.append({"event": e})
        elif isinstance(e, dict):
            out.append(e)
    return out


def _acoustic_events_labels(events):
    """Extract flat label list from acoustic events (any format). For embedding/keyword search."""
    return [e["event"] if isinstance(e, dict) else str(e) for e in (events or [])]


def b64_uri(path):
    with open(path, "rb") as f:
        return "data:video/mp4;base64," + base64.b64encode(f.read()).decode()


def http_status(msg, code):
    """Whether `code` appears in `msg` as a status code rather than as part of some longer token.

    Bare substring matching is unsafe because every DashScope error carries a hex request_id, and
    'aafbd030-429c-9aa3-…' contains "429" — which would read a 404 as throttling and retry it. Hence the
    non-alphanumeric neighbours.
    """
    return re.search(rf"(?<![0-9a-z]){code}(?![0-9a-z])", str(msg).lower()) is not None


def is_rate_limit(msg):
    """Detect throttling/quota errors across DashScope's various wordings."""
    m = str(msg).lower()
    if http_status(m, "429"):
        return True
    keys = (
        "insufficient_quota",
        "quota",
        "ratelimit",
        "rate limit",
        "too many requests",
        "request rate",
        "increased too quickly",
        "scale requests",
        "smoothly",
        "throttl",
        "flow control",
        "limit exceeded",
    )
    return any(k in m for k in keys)


def backoff(attempt, is_rate):
    """Exponential backoff with jitter. Rate-limit errors wait much longer (smoother)."""
    base, cap = (8.0, 90.0) if is_rate else (3.0, 20.0)
    delay = min(base * (2 ** (attempt - 1)), cap)
    return delay + random.uniform(0, 0.3 * delay)


_RETRY_TL = threading.local()


def retry_reset():
    """Start a fresh per-thread backoff log (call at each model-call entry)."""
    _RETRY_TL.events = []


def _retry_note(attempt, wait, reason):
    """Record ONE backoff wait to the current thread's log and echo to stdout."""
    evs = getattr(_RETRY_TL, "events", None)
    if evs is None:
        evs = _RETRY_TL.events = []
    evs.append({"attempt": int(attempt), "wait": round(float(wait), 2), "reason": reason})
    diag(f"[BACKOFF] #{attempt} wait={round(float(wait), 2)}s ({reason})", flush=True)


def sleep_note(attempt, wait, reason):
    """Record + actually sleep. Use in place of time.sleep at retry points."""
    _retry_note(attempt, wait, reason)
    time.sleep(wait)


# ============================================================ SLIDING-WINDOW CLIPPING
def probe_duration(src):
    """Return video duration in seconds via ffprobe (timeout-guarded), or 0.0 on failure."""
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                src,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return float((r.stdout or "0").strip())
    except Exception as e:
        diag(f"[CLIP] probe_duration failed: {str(e)[:120]}", flush=True)
        return 0.0


# ============================================================ MODEL CALLS
STREAM_STALL_SEC = int(os.environ.get("MEM_STREAM_STALL", "150") or "150")  # abort+retry if no chunk for this long


def iter_deadline(stream, stall=STREAM_STALL_SEC):
    """Yield chunks from a streaming response, raising TimeoutError if NO new chunk arrives for
    `stall` seconds — catches mid-stream vLLM stalls the SDK read-timeout misses (server trickles
    keepalives → read-timeout never fires; observed 13-min hangs on some clips). The producing
    iteration runs in a daemon thread; on stall we abandon it and let the caller's retry re-issue."""
    import queue as _queue
    import threading as _th

    q = _queue.Queue()

    def _worker():
        try:
            for ch in stream:
                q.put((0, ch))
            q.put((1, None))
        except Exception as e:
            q.put((2, e))

    _th.Thread(target=_worker, daemon=True).start()
    while True:
        try:
            kind, val = q.get(timeout=stall)
        except _queue.Empty:
            raise TimeoutError(f"stream stalled >{stall}s (no chunk)")
        if kind == 0:
            yield val
        elif kind == 1:
            return
        else:
            raise val


ANON_NAMES = os.environ.get("MEM_ANON_ENTITIES", "1") == "1"

SALIENT_EMO = {"neutral", "unknown", ""}

SALIENT_TONE = {"calm", "neutral", "unknown", ""}

NAME_STOP = {
    "i",
    "the",
    "a",
    "an",
    "so",
    "um",
    "uh",
    "oh",
    "well",
    "yeah",
    "yes",
    "no",
    "okay",
    "ok",
    "hmm",
    "and",
    "but",
    "do",
    "does",
    "did",
    "is",
    "are",
    "was",
    "were",
    "if",
    "it",
    "he",
    "she",
    "we",
    "they",
    "you",
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank",
    "please",
    "this",
    "that",
    "what",
    "who",
    "when",
    "where",
    "why",
    "how",
    "mr",
    "ms",
    "mrs",
    "sir",
    "good",
    "great",
    "sorry",
    "right",
    "sure",
    "maybe",
    "also",
    "then",
    "now",
    "here",
    "there",
    "our",
    "your",
    "my",
    "his",
    "her",
    "their",
    "let",
    "ooh",
    "wow",
    "yep",
    "nope",
    "alright",
    "alrighty",
    "first",
    "second",
    "next",
    "last",
    "one",
    "two",
    "three",
    "china",
    "chinese",
    "italian",
    "english",
    "usd",
    "mcdonald",
    "monday",
    "friday",
    "everyone",
    "nothing",
    "yesterday",
    "today",
    "tomorrow",
    # extra common sentence-initial / interjection words mis-captured as names (ledger de-noising)
    "actually",
    "mhm",
    "mmm",
    "just",
    "from",
    "although",
    "really",
    "neither",
    "could",
    "would",
    "should",
    "wait",
    "put",
    "don",
    "dont",
    "linear",
    "old",
    "because",
    "close",
    "careful",
    "finally",
    "fine",
    "look",
    "make",
    "not",
    "see",
    "sounds",
    "take",
    "too",
    "walking",
    "waiting",
    "appreciate",
    "can",
    "every",
    "everything",
    "day",
    "impossible",
    "cannot",
    "never",
    "always",
    "little",
    "perhaps",
    "anyway",
    "godness",
    "damn",
    "dang",
    "yummy",
    "labor",
    "laughter",
    "tomato",
    "honey",
    "everybody",
    "anybody",
    "somebody",
    "something",
    "someone",
}


# ============================================================ EMBEDDINGS
def embed_texts(client, texts, max_retries=4, batch=10):
    """Return list of vectors via DashScope embeddings, or None on failure.

    A None client is that same failure signal, reported without the wait: one guard here covers every
    call site, and each of them already reads None as "no dense pool, use BM25".
    """
    if client is None:
        return None
    if not texts:
        return []
    out = []
    for i in range(0, len(texts), batch):
        chunk = [t if t.strip() else " " for t in texts[i : i + batch]]
        vec = None
        for attempt in range(1, max_retries + 1):
            try:
                r = client.embeddings.create(model=EMBED_MODEL, input=chunk)
                vec = [d.embedding for d in r.data]
                break
            except Exception as e:
                _ir = is_rate_limit(e)
                if not _ir and attempt >= 2:
                    return None
                sleep_note(attempt, min(2 * attempt, 8), "rate" if _ir else "error")
        if vec is None:
            return None
        out.extend(vec)
    return out


# ---------------------------------------------------------- vectors on disk
# A vector is stored as "f32:<base64>", a LOSSLESS re-encoding rather than a compression: the endpoint
# returns float32, so the 17 digits a JSON array prints carry no extra information. What it saves is
# text — 5 466 bytes per 1024-dim vector against 22 786 as decimals, and vectors are ~63% of a store.
#
# The dtype tag is checked, not assumed: reading the bytes at the wrong width would yield a plausible
# vector and a silently wrong ranking, which is worse than an exception.
EMB_DTYPE = np.float32

_EMB_TAG = "f32:"


def emb_encode(v):
    """Vector → "f32:<base64>". Accepts a list or an ndarray; None and an already-encoded string
    pass through, so this is safe to apply to a container of mixed provenance."""
    if v is None or isinstance(v, str):
        return v
    a = np.asarray(v, dtype=EMB_DTYPE)
    return _EMB_TAG + base64.b64encode(a.tobytes()).decode("ascii") if a.size else None


def emb_decode(x):
    """ "f32:<base64>" → float32 ndarray. A JSON array means a library written before this encoding
    existed; it is decoded to an ndarray as well, so callers see ONE in-memory type no matter which
    format the memory on disk uses. The array is read-only (it views the decoded buffer) — nothing
    mutates a vector in place, build_index assigns a whole new one."""
    if x is None:
        return None
    if isinstance(x, np.ndarray):
        return x if x.dtype == EMB_DTYPE else x.astype(EMB_DTYPE)
    if isinstance(x, str):
        if not x.startswith(_EMB_TAG):
            raise ValueError(f"unrecognised embedding encoding: {x[:16]!r}")
        return np.frombuffer(base64.b64decode(x[len(_EMB_TAG) :]), dtype=EMB_DTYPE)
    return np.asarray(x, dtype=EMB_DTYPE) if len(x) else None


def _emb_in(d):
    """Decode a record's vector in place, on the way in from JSON. In place is safe: the dict was
    just parsed and nobody else holds a reference to it yet."""
    if d.get("emb") is not None:
        d["emb"] = emb_decode(d["emb"])
    return d


class StoreBase:
    """The memory containers, and the one serialization both paths have to agree on.

    entities   canonical people — person_id, resolved name, appearance, attributes
    episodic   one composite {visual, audio} record per clip, each carrying its embedding
    semantic   induced triples, deduplicated by key
    scene_env  durable environment items, recalled on demand rather than always resident
    clips      pointers to the clip files, so an answer can re-watch the source

    The build subclasses this with the setters and the index builder (store.MemoryStore); the query
    side subclasses it with retrieval (mem_core.MemoryStore). from_dict sits here because both sides
    read a store off disk — the build to resume one, the server to answer from one — so the reader and
    the writer of store.json are guaranteed to describe the same fields.
    """

    def __init__(self):
        self.global_summary = ""  # rolling summary; maintained but not consumed (see DEVIATIONS.md)
        self.global_nodes = []  # resolution-decaying nodes [{level, text, t0, t1}]
        self.scene_env = []
        self.entities = []
        self.episodic = []  # each record gets an "emb"
        self.semantic = []  # each triple gets an "emb"
        self.clips = []  # [{idx, path, win_start, win_end}] — what replay re-watches
        self.driver_state = None  # last stateful-driver state (incremental continuation)
        self.processed_sec = 0.0  # total video seconds ingested so far (global time offset)
        self.dense_ok = False
        self.name_ledger = {}  # streaming name-alignment evidence ledger (bounded; see NAME ALIGN)
        self._by_id = {}
        self._by_name = {}

    def set_entities(self, entities):
        self.entities = entities or []
        self._by_id = {e.get("person_id"): e for e in self.entities}
        self._by_name = {}
        for e in self.entities:
            if e.get("name"):
                self._by_name.setdefault(e["name"].lower(), e)

    @classmethod
    def from_dict(cls, d):
        """Rebuild a MemoryStore from to_dict() output (hydrated from disk).

        Vectors decode to float32 ndarrays whichever way they were written — "f32:<base64>" or a bare
        JSON array — so a store.json in either encoding loads unchanged, and is rewritten in the current
        one only when something saves it."""
        s = cls()
        s.global_summary = d.get("global_summary", "") or ""
        s.global_nodes = list(d.get("global_nodes", []) or [])  # v3.1+; older libs → [] (back-compat)
        s.scene_env = list(d.get("scene_env", []) or [])
        s.set_entities(d.get("entities", []) or [])  # rebuilds _by_id / _by_name
        s.episodic = [_emb_in(r) for r in (d.get("episodic") or [])]
        s.semantic = [_emb_in(t) for t in (d.get("semantic") or [])]
        s.clips = d.get("clips", []) or []
        s.driver_state = d.get("driver_state")
        s.processed_sec = float(d.get("processed_sec", 0) or 0)
        s.dense_ok = bool(d.get("dense_ok", False))
        s.name_ledger = d.get("name_ledger", {}) or {}  # v3+; older libs → {} (back-compat)
        return s

    # ---------- text extraction from a composite record ----------
    @staticmethod
    def _ep_text(rec):
        p = rec.get("parsed") or {}
        vis = p.get("visual", {}) or {}
        aud = p.get("audio", {}) or {}
        parts = [vis.get("visual_caption", "")]
        parts += vis.get("actions", []) or []
        parts += vis.get("target_range_delta", []) or []
        for e in vis.get("key_entities", []) or []:
            parts.append(" ".join(filter(None, [e.get("name"), e.get("ref"), e.get("attributes")])))
        parts.append(aud.get("transcript", "") or "")
        for u in aud.get("utterances", []) or []:
            parts.append(u.get("text", ""))
        ac = aud.get("acoustic", {}) or {}
        parts += _acoustic_events_labels(ac.get("events", []))
        parts.append(ac.get("scene", "") or "")
        return " ".join(x for x in parts if x)
