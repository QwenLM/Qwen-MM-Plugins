"""Tests for the omni-memory MCP server (long-video audio-visual memory).

Two halves. The protocol tests mirror test_video_memory.py: the SDK bridge must expose the tool set
and fail *gracefully* when no memory exists, rather than crashing the process. The rest exercise the
decisions this capability makes before it ever reaches a model — how a vector is stored, how a watch
payload is sized, and how a failed call is classified — because each of those has a wrong answer that
looks plausible, and none of them need an endpoint to check.

Building or querying real memory is out of scope here (it needs a video plus DASHSCOPE_API_KEY).
"""

import contextlib
import importlib
import importlib.util
import json
import os
import pathlib
import sys

import numpy as np
import pytest
from conftest import REPO_ROOT, mcp_call

from qwen_mm_plugins_omni_memory import config, mem_core, omni_core, watch

OM_SERVER_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "omni-memory", "qwen_mm_plugins_omni_memory")

# Auto-discovered from the tools/ subpackage. watch_and_answer is the one that needs no memory.
OM_TOOLS = {
    "get_memory_status",
    "get_memory_overview",
    "get_people",
    "get_person_dialogue",
    "get_timeline",
    "get_moment",
    "search_memory",
    "search_dialogue",
    "search_facts",
    "plan_and_search",
    "replay_and_answer",
    "watch_and_answer",
}


def test_omni_chat_config_uses_shared_dashscope_settings(monkeypatch):
    values = {
        "DASHSCOPE_BASE_URL": "https://dashscope.example/v1",
        "DASHSCOPE_API_KEY": "dashscope-key",
        "QWEN_MM_API_OMNI_MODEL": "qwen-omni-test",
        # Legacy omni-specific names must no longer override the shared settings.
        "OMNI_BASE_URL": "https://legacy.example/v1",
        "OMNI_API_KEY": "legacy-key",
        "OMNI_MODEL": "legacy-model",
    }
    monkeypatch.setattr(config, "get_env", lambda name, default=None: values.get(name, default))

    assert config.chat_config() == (
        "https://dashscope.example/v1",
        "qwen-omni-test",
        "dashscope-key",
    )


def test_om_server_lists_tools():
    result = mcp_call(OM_SERVER_DIR, lambda s: s.list_tools())
    assert {t.name for t in result.tools} == OM_TOOLS


def test_om_server_graceful_without_memory(tmp_path):
    # No memory at <video>.memory/ → the tool must report that as data, not raise. get_memory_status
    # is the one tool whose whole job is answering "is there a memory?", so a missing one is a normal
    # result rather than an error.
    video = tmp_path / "nothing.mp4"
    video.write_bytes(b"")
    result = mcp_call(OM_SERVER_DIR, lambda s: s.call_tool("get_memory_status", {"video_path": str(video)}))
    assert not result.isError
    assert any(getattr(b, "type", None) == "text" for b in result.content)


# ───────────────────────────────────────────────────────── vectors on disk


def test_embeddings_round_trip_bit_exactly():
    """The base64 form must be a lossless re-encoding, not a lossy compression: the endpoint returns
    float32, so nothing is thrown away and retrieval cannot shift."""
    v = [0.1, -0.2, 3e-8, 0.0, 1.0]
    decoded = omni_core.emb_decode(omni_core.emb_encode(v))
    assert np.array_equal(decoded, np.asarray(v, dtype=np.float32))
    assert decoded.dtype == np.float32


def test_embeddings_accept_the_pre_encoding_list_form():
    """A library written before the base64 encoding stores a JSON array. It must load unchanged, and
    as the same in-memory type, so callers never branch on which format is on disk."""
    store = mem_core.MemoryStore.from_dict({"episodic": [{"idx": 0, "emb": [0.5, 0.25]}], "semantic": []})
    assert isinstance(store.episodic[0]["emb"], np.ndarray)
    assert np.array_equal(store.episodic[0]["emb"], np.asarray([0.5, 0.25], dtype=np.float32))
    # Re-serializing it in the new form is the build's job now — see
    # test_what_the_build_writes_is_what_the_query_side_reads.


def test_embeddings_reject_an_unknown_dtype_tag():
    """Reading the bytes at the wrong width would yield a plausible vector and a silently wrong
    ranking, so the tag is checked rather than assumed."""
    with pytest.raises(ValueError):
        omni_core.emb_decode("f16:AAAA")


# ───────────────────────────────────────────────────────── watch payload sizing


def _info(minutes, *, height=480, v_kbps=2000, a_kbps=128, size=10**9):
    return {
        "duration": minutes * 60,
        "size": size,
        "width": int(height * 16 / 9),
        "height": height,
        "v_kbps": v_kbps,
        "a_kbps": a_kbps,
        "channels": 2,
    }


def test_watch_encode_never_inflates_the_source():
    """A fixed target bitrate could exceed the source's own and pad the file while double-compressing
    it — measured at 30% growth on a 480p/499k sample, which pushed it past the request limit."""
    plan = watch.plan_watch_encode(_info(5, v_kbps=499, a_kbps=64, size=int(21.2 * 1048576)))
    assert plan["v_kbps"] <= 499
    assert plan["a_kbps"] <= 64
    assert plan["height"] <= 480


@pytest.mark.parametrize("minutes", [2, 5, 10, 15])
def test_watch_encode_lands_inside_the_payload_budget(minutes):
    plan = watch.plan_watch_encode(_info(minutes))
    b64_mb = (plan["v_kbps"] + plan["a_kbps"]) * 1000 / 8 * minutes * 60 * 4 / 3 / 1048576
    assert b64_mb <= watch.WATCH_MAX_B64_MB


def test_watch_encode_refuses_what_cannot_fit_and_says_why():
    """Past the last rung there is no quality left to trade, so the caller is told the arithmetic and
    sent to build a memory instead of handed an unreadably compressed answer."""
    plan = watch.plan_watch_encode(_info(40))
    assert "error" in plan
    assert "affords" in plan["error"]


def test_watch_encode_clamps_rather_than_skipping_for_a_small_source():
    """A source shorter than every rung must not fall through the whole ladder — for it the rungs are
    bitrate floors at its own height."""
    plan = watch.plan_watch_encode(_info(10, height=240, v_kbps=300))
    assert "error" not in plan
    assert plan["height"] <= 240
    assert plan["v_kbps"] <= 300


def test_watch_encode_leaves_an_already_small_source_alone():
    plan = watch.plan_watch_encode(_info(2, height=360, v_kbps=200, a_kbps=32, size=3 * 1048576))
    assert plan == {"reuse": True}


# ───────────────────────────────────────────────────────── failure classification


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Error code: 429 - rate limit reached", "rate"),
        ("insufficient_quota", "rate"),
        # A request_id is hex, so it contains "429" often enough to matter: bare substring matching
        # read this 404 as throttling and retried what the endpoint refuses every time.
        ("404 model_not_found, request_id: aafbd030-429c-9aa3-ade4", "config"),
        ("Error code: 401 - invalid_api_key", "config"),
        ("TimeoutError: stream stalled >150s (no chunk)", "timeout"),
        ("Error code: 400 - context length exceeded", "reject"),
    ],
)
def test_failure_is_classified_by_what_the_caller_should_do_next(message, expected):
    if omni_core.is_rate_limit(message):
        kind = "rate"
    elif watch._is_misconfigured(message):
        kind = "config"
    elif watch._is_stall(message):
        kind = "timeout"
    else:
        kind = "reject"
    assert kind == expected


@pytest.mark.parametrize(
    ("kind", "build_helps"),
    [("reject", True), ("timeout", True), ("empty", True), ("rate", False), ("config", False)],
)
def test_only_failures_a_build_would_survive_suggest_building(kind, build_helps):
    """Throttling is transient and a wrong endpoint breaks a build too, so neither should send the
    caller off to spend tens of minutes reproducing the error."""
    assert watch.WatchError("x", kind).build_helps is build_helps


# ───────────────────────────────────────────────────────── routing by duration


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(3, "watch_and_answer"), (20, "script/build_memory/build_memory.py"), (45, "past the")],
)
def test_status_recommends_watching_or_building_by_length(minutes, expected):
    """get_memory_status is the mandatory first call, so it is where the length bands have to be
    answerable — before this it unconditionally said "build it", which is the wrong advice for a
    two-minute clip."""
    from qwen_mm_plugins_omni_memory.tools.get_memory_status import _no_memory_next_step

    assert expected in _no_memory_next_step("/tmp/x.mp4", minutes * 60)


def test_status_falls_back_to_building_when_the_duration_is_unknown():
    from qwen_mm_plugins_omni_memory.tools.get_memory_status import _no_memory_next_step

    assert _no_memory_next_step("/tmp/x.mp4", 0).startswith("build it:")


# ─────────────────────────────────────────────────── where a build actually lands

_BUILD_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "omni-memory", "skill", "script", "build_memory")
_BUILD_SCRIPT = os.path.join(_BUILD_DIR, "build_memory.py")

# The build's modules are flat and generically named, and video-memory's build directory holds an
# env_config.py and a prompts.py of its own. Whichever test ran first would otherwise own the name for
# the rest of the session — that is how these tests started failing only in a full run, importing
# video-memory's env_config and finding no chat_config() on it.
_BUILD_MODULES = (
    "clipping",
    "env_config",
    "llm",
    "omni_core",
    "pipeline",
    "prompts",
    "stages",
    "storage",
    "store_writer",
)


@contextlib.contextmanager
def _build_side_path():
    """Make the build's flat modules importable under their own names, then put sys.modules back."""
    saved = {n: sys.modules.pop(n, None) for n in _BUILD_MODULES}
    sys.path.insert(0, _BUILD_DIR)
    try:
        yield
    finally:
        sys.path.remove(_BUILD_DIR)
        for name, mod in saved.items():
            sys.modules.pop(name, None)
            if mod is not None:
                sys.modules[name] = mod


class _StopAfterLayout(Exception):
    """Stands in for slicing, so a test stops once build_one has fixed the storage root."""


def _load_build():
    """Import the build script the way RUNNING it does: only its own directory on sys.path.

    Call inside _build_side_path(). Loading it with the server package importable would test a layout
    some harnesses never produce — several install a skill by copying skill/ alone, so the build has to
    resolve every module it needs as a sibling. Putting only _BUILD_DIR on the path makes that a test
    rather than a hope.
    """
    spec = importlib.util.spec_from_file_location("_om_build_memory", _BUILD_SCRIPT)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def _storage_root_for(monkeypatch, video, namespace, config_file):
    """Run build_one far enough to resolve the layout, then bail out before it does any real work.

    The layout block is the whole subject here: it decides the directory the memory is written to,
    and the query side has to arrive at the same one — through two different config readers now, which
    is exactly why both caches are reset below.
    """
    monkeypatch.delenv("MEM_LOCAL_DIR", raising=False)
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config_file))
    monkeypatch.setattr("shared.env._config_cache", None)  # the query side parses the file once

    def _stop(*_args, **_kwargs):
        raise _StopAfterLayout

    with _build_side_path():
        build = _load_build()
        monkeypatch.setattr(build.config, "_CONFIG", None)  # and so does the build's own env_config
        monkeypatch.setattr(build, "slice_video", _stop)
        with pytest.raises(_StopAfterLayout):
            build.build_one(
                video, mode="new", namespace=namespace, window=30, step=25, height=480, rollup_k=6, max_clips=0
            )
        return build.storage.get_backend().root


def test_namespace_build_lands_where_the_query_side_looks(tmp_path, monkeypatch):
    """MEM_LOCAL_DIR set only in the config file must not split the build from the query.

    It is one of the four fields `--setup` writes there, so "the value lives in the config file and
    not the environment" is the documented way to set it — and the only way a GUI-launched harness
    can. Reading os.environ here instead put the memory in ~/.omni-memory while the server looked in
    the configured root: a build that runs for tens of minutes, reports success, and cannot be found.
    """
    from qwen_mm_plugins_omni_memory import service

    library = tmp_path / "library"
    config_file = tmp_path / "config"
    config_file.write_text(f"MEM_LOCAL_DIR={library}\n", encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")

    root = _storage_root_for(monkeypatch, video, "my_stream", config_file)

    assert root == str(library)
    assert service.memory_dir(namespace="my_stream") == library / "my_stream"


def test_namespace_defaults_beside_the_video(tmp_path, monkeypatch):
    from qwen_mm_plugins_omni_memory import service

    config_file = tmp_path / "config"
    config_file.write_text("", encoding="utf-8")
    video = tmp_path / "movies" / "clip.mp4"
    video.parent.mkdir()
    video.write_bytes(b"")

    root = _storage_root_for(monkeypatch, video, "my_stream", config_file)

    assert root == str(video.parent)
    monkeypatch.delenv("MEM_LOCAL_DIR")
    assert service.memory_dir(video_path=str(video), namespace="my_stream") == video.parent / "my_stream"
    with pytest.raises(ValueError, match="requires video_path"):
        service.memory_dir(namespace="my_stream")


def test_per_video_build_still_lands_next_to_the_video(tmp_path, monkeypatch):
    """The default layout roots at the VIDEO's directory, never at MEM_LOCAL_DIR.

    build_one publishes that root by assigning os.environ["MEM_LOCAL_DIR"], which is the only way
    storage.get_backend() learns it. Dropping that assignment reads like dead code and would send
    every per-video build into the shared library instead — where no query would ever look for it.
    """
    from qwen_mm_plugins_omni_memory import service

    config_file = tmp_path / "config"
    config_file.write_text(f"MEM_LOCAL_DIR={tmp_path / 'library'}\n", encoding="utf-8")
    video = tmp_path / "movies" / "clip.mp4"
    video.parent.mkdir()
    video.write_bytes(b"")

    root = _storage_root_for(monkeypatch, video, None, config_file)

    assert root == str(video.parent)
    assert service.memory_dir(video_path=str(video)) == video.parent / "clip.mp4.memory"


# ────────────────────────────────────────── what a not-found index is told back


def _memory_with(tmp_path, name, episodic):
    """A minimal on-disk memory. Separate directories per case, so the mtime-keyed store cache never
    hands one case the other's records."""
    video = tmp_path / f"{name}.mp4"
    video.write_bytes(b"")
    mdir = tmp_path / f"{name}.mp4.memory"
    mdir.mkdir()
    (mdir / "store.json").write_text(
        json.dumps({"episodic": episodic, "clips": [], "entities": [], "semantic": []}), encoding="utf-8"
    )
    return str(video)


def test_get_moment_offers_a_range_only_when_there_is_one(tmp_path):
    """`available_range` exists to tell a caller what it could have asked for instead, so it reaches
    exactly the caller whose index was just not found. On an empty container it used to answer
    [0, 0] — which reads as "index 0 exists", the opposite of true, and sends that caller to another
    index that is equally absent. Absent key, and `missing_idxs` against `count: 0`, is what is known.
    """
    from qwen_mm_plugins_omni_memory.tools.get_moment import get_moment

    empty = get_moment(video_path=_memory_with(tmp_path, "empty", []), idxs=[5])
    assert empty["count"] == 0
    assert empty["missing_idxs"] == [5]
    assert "available_range" not in empty

    records = [{"idx": i, "win_start": i * 25.0, "win_end": i * 25.0 + 30} for i in (2, 3, 4)]
    populated = get_moment(video_path=_memory_with(tmp_path, "filled", records), idxs=[9])
    assert populated["missing_idxs"] == [9]
    assert populated["available_range"] == [2, 4]


# ─────────────────────────────────── querying a memory with no embedding key


def test_no_embedding_credential_steps_aside_instead_of_paying_for_a_401(monkeypatch):
    """Dense retrieval must decline instantly when nothing is configured to serve it.

    The cookbook offers querying a memory built elsewhere without a key, and the ranking does fall
    back to BM25 — but it used to get there by way of a placeholder key, two requests that could only
    401, and the 2s backoff between them, once per query. `plan_and_search` embeds each query it is
    given, so a four-angle plan paid eight seconds to arrive at the answer it would have had anyway.
    """
    dashscope_only = {"DASHSCOPE_BASE_URL": "https://dashscope.example/v1"}
    monkeypatch.setattr(config, "get_env", lambda name, default=None: dashscope_only.get(name, default))
    assert config.embed_config() == ("https://dashscope.example/v1", "text-embedding-v4", None)

    # …but a self-hosted embedding endpoint may legitimately want no credential, and the OpenAI
    # client rejects None and "" outright, so the placeholder has to survive on that branch.
    self_hosted = {**dashscope_only, "EMBED_BASE_URL": "http://embed.internal/v1"}
    monkeypatch.setattr(config, "get_env", lambda name, default=None: self_hosted.get(name, default))
    assert config.embed_config() == ("http://embed.internal/v1", "text-embedding-v4", "EMPTY")


def test_embedding_with_no_key_makes_no_request_and_does_not_back_off(monkeypatch):
    slept = []
    monkeypatch.setattr(omni_core, "sleep_note", lambda *a, **k: slept.append(a))

    monkeypatch.setattr(omni_core, "_EMBED_KEY", None)
    assert omni_core.get_embed_client() is None

    # None is the same signal a failed call gives, which every call site already reads as "no dense
    # pool" — reached here without a request, and so without the retry loop's sleep.
    assert omni_core.embed_texts(None, ["anything"]) is None
    assert slept == []

    monkeypatch.setattr(omni_core, "_EMBED_KEY", "sk-real")
    assert omni_core.get_embed_client() is not None


# ─────────────────────────────── the boundary between the build and the server


def test_what_the_build_writes_is_what_the_query_side_reads():
    """The build and the server hold SEPARATE copies of StoreBase, so store.json between them is the
    one thing they must never disagree about. Written here by the build's MemoryStore, through real
    JSON, and read back by the server's.

    The vector is the part that can silently rot: it goes out as "f32:<base64>" and has to come back a
    float32 ndarray. A JSON array on the way in covers a library built before that encoding existed.
    """
    with _build_side_path():
        writer = importlib.import_module("store_writer")
        built = writer.MemoryStore.from_dict({"episodic": [{"idx": 0, "emb": [0.5, 0.25]}], "semantic": []})
        built.set_entities([{"person_id": "P001", "name": "Ada"}])
        on_disk = json.loads(json.dumps(built.snapshot_for_async()))

    assert on_disk["episodic"][0]["emb"].startswith("f32:")

    read_back = mem_core.MemoryStore.from_dict(on_disk)
    assert isinstance(read_back.episodic[0]["emb"], np.ndarray)
    assert np.array_equal(read_back.episodic[0]["emb"], np.asarray([0.5, 0.25], dtype=np.float32))
    assert read_back.get_entity("Ada")["person_id"] == "P001"


def _below_marker(path, marker):
    """Everything after the line holding `marker` — the region two copies have to share verbatim."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    _, sep, rest = text.partition(marker)
    assert sep, f"{path} no longer has its {marker!r} marker"
    return rest.partition("\n")[2]


def test_the_two_copies_of_omni_core_stay_identical():
    """Neither side can import the other — the build may be installed without the package, and the
    wheel does not ship the skill — so what both need is a copy. This is what keeps it a copy rather
    than two files that used to be one.
    """
    marker = "IDENTICAL IN BOTH COPIES BELOW THIS LINE"
    server = _below_marker(os.path.join(OM_SERVER_DIR, "omni_core.py"), marker)
    build = _below_marker(os.path.join(_BUILD_DIR, "omni_core.py"), marker)
    assert server == build, "omni_core.py has drifted between the server package and the skill"


def test_the_two_config_readers_stay_identical():
    """Same deal for the settings getters: the build and the server must resolve the same endpoint,
    model and library root from the same keys, or a build lands where no query looks."""
    marker = "IDENTICAL IN env_config.py BELOW THIS LINE"
    server = _below_marker(os.path.join(OM_SERVER_DIR, "config.py"), marker)
    build = _below_marker(os.path.join(_BUILD_DIR, "env_config.py"), marker)
    # env_config is also runnable, for a shell launcher that needs the keys exported; that tail is
    # build-only and stops the shared region.
    build = build.partition('if __name__ == "__main__":')[0]
    assert server.strip() == build.strip(), "the build and the server read settings differently now"
