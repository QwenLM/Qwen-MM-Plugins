"""Offline unit tests for the video-memory build pipeline (P2.1).

Targets src/capabilities/video-memory/skill/script/build_memory/ — the flat modules the
build runs with (not the server package copies). Only pure logic is exercised: schema
construct/serialize round-trips, time parsing, JSON extraction, BM25/cosine search with the
embedding network boundary monkeypatched, and NPZ save/load. Nothing here needs
DASHSCOPE_API_KEY, ffmpeg, or the network. Modules whose imports pull heavy/optional deps
are loaded through a skipping fixture, mirroring tests/test_build_merge.py.
"""

import importlib
import json
import os
import sys
from dataclasses import asdict

import pytest
from conftest import REPO_ROOT

_BUILD_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "video-memory", "skill", "script", "build_memory")


def _import_build_module(name: str):
    """Import a flat build-pipeline module (the dir is not a package / not on sys.path)."""
    if _BUILD_DIR not in sys.path:
        sys.path.insert(0, _BUILD_DIR)
    try:
        return importlib.import_module(name)
    except Exception as e:  # noqa: BLE001 — optional build-only deps may be absent
        pytest.skip(f"{name} not importable: {e}")


@pytest.fixture(scope="module")
def time_utils():
    return _import_build_module("time_utils")


@pytest.fixture(scope="module")
def schema():
    return _import_build_module("schema")


@pytest.fixture(scope="module")
def embeddings():
    pytest.importorskip("numpy")
    return _import_build_module("embeddings")


@pytest.fixture(scope="module")
def llm_client():
    pytest.importorskip("requests")
    return _import_build_module("llm_client")


@pytest.fixture(scope="module")
def build_graph():
    pytest.importorskip("numpy")
    pytest.importorskip("requests")
    return _import_build_module("build_graph")


@pytest.fixture(scope="module")
def pipeline_worker(build_graph):
    return _import_build_module("pipeline_worker")


# ── time_utils: timestamp parsing/formatting ─────────────────────────


def test_time_str_to_sec_formats(time_utils):
    assert time_utils.time_str_to_sec("01:01:01.5") == 3661.5
    assert time_utils.time_str_to_sec("02:30.25") == 150.25
    assert time_utils.time_str_to_sec("42") == 42.0


def test_sec_to_time_str(time_utils):
    assert time_utils.sec_to_time_str(3661) == "01:01:01"
    assert time_utils.sec_to_time_str(0) == "00:00:00"
    assert time_utils.sec_to_time_str(59.9) == "00:00:59"  # truncates, not rounds


def test_time_roundtrip_whole_seconds(time_utils):
    for sec in (0, 1, 59, 60, 3599, 3600, 86399):
        assert time_utils.time_str_to_sec(time_utils.sec_to_time_str(sec)) == float(sec)


# ── llm_client.extract_json: fence/prose-tolerant JSON parsing ───────


def test_extract_json_plain_and_fenced(llm_client):
    assert llm_client.extract_json('{"a": 1}') == {"a": 1}
    assert llm_client.extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert llm_client.extract_json('```\n{"b": 2}\n```') == {"b": 2}


def test_extract_json_strips_think_and_prose(llm_client):
    assert llm_client.extract_json('<think>reasoning</think>\n{"a": 1}') == {"a": 1}
    assert llm_client.extract_json('Sure, here it is:\n{"a": 1}\nHope that helps!') == {"a": 1}


def test_extract_json_garbage_raises(llm_client):
    with pytest.raises(json.JSONDecodeError):
        llm_client.extract_json("no json anywhere")


# ── schema: data-model construction / save-load round-trip ───────────


def _make_memory(schema):
    sg = schema.Subgraph(
        macro_id="macro_0000",
        micro_events=[
            schema.MicroEvent(
                event_id="evt_001",
                event_type="action",
                time_range=[0.0, 5.0],
                subject="cat",
                object="mat",
                action="sits",
                description="a cat sits on the mat",
                macro_id="macro_0000",
            )
        ],
        entities=[
            schema.Entity(
                entity_id="ent_001",
                name="cat",
                entity_type="animal",
                description="an orange cat",
                macro_id="macro_0000",
            )
        ],
        on_screen_texts=[
            schema.OnScreenText(text_id="ocr_001", text="INTRO", time_range=[0.0, 2.0], macro_id="macro_0000")
        ],
        edges=[schema.Edge(source_id="ent_001", target_id="evt_001", relation_label="agent", relation_type="does")],
    )
    return schema.HierarchicalGraphMemory(
        video_key="vid",
        video_path="/tmp/vid.mp4",
        root=schema.VideoRoot(title="T", description="D", themes=["th"], emotional_tone="calm"),
        super_events=[
            schema.SuperEvent(
                super_id="super_00", label="S", sub_macro_ids=["macro_0000", "macro_0001"], time_range=[0.0, 20.0]
            )
        ],
        macro_events=[
            schema.MacroEvent(
                macro_id="macro_0000", label="A", time_range=[0.0, 10.0], subgraph=sg, super_id="super_00"
            ),
            schema.MacroEvent(macro_id="macro_0001", label="B", time_range=[10.0, 20.0], super_id="super_00"),
        ],
        macro_relations=[schema.MacroRelation(source="macro_0000", target="macro_0001", type="causes", reason="r")],
        super_relations=[],
    )


def test_memory_save_load_roundtrip(schema, tmp_path):
    mem = _make_memory(schema)
    path = str(tmp_path / "graph_memory.json")
    mem.save(path)
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp"), "atomic save must not leave the .tmp behind"

    loaded = schema.HierarchicalGraphMemory.load(path)
    assert asdict(loaded) == asdict(mem), "save → load must be a lossless round-trip"


def test_memory_save_is_valid_json(schema, tmp_path):
    path = str(tmp_path / "graph_memory.json")
    _make_memory(schema).save(path)
    with open(path) as f:
        data = json.load(f)
    assert data["video_key"] == "vid"
    assert [m["macro_id"] for m in data["macro_events"]] == ["macro_0000", "macro_0001"]


def test_get_all_nodes_covers_entity_event_ocr(schema):
    nodes = _make_memory(schema).get_all_nodes()
    by_type = {}
    for n in nodes:
        by_type.setdefault(n["node_type"], []).append(n)
    assert set(by_type) == {"entity", "event", "on_screen_text"}
    # every node carries the macro anchor + non-empty text for embedding
    for n in nodes:
        assert n["macro_id"] == "macro_0000"
        assert n["node_id"] and n["text"].strip()


def test_get_all_nodes_chunks_long_asr(schema):
    mem = _make_memory(schema)
    # >500 chars of sentences → must split into multiple asr chunks on sentence boundaries
    mem.macro_events[0].asr_text = " ".join(f"Sentence number {i} spoken in the video." for i in range(30))
    mem.macro_events[1].asr_text = "hi"  # <3 chars → skipped entirely
    asr_nodes = [n for n in mem.get_all_nodes() if n["node_type"] == "asr_text"]
    assert len(asr_nodes) >= 2, "long ASR text must be chunked"
    assert all(n["macro_id"] == "macro_0000" for n in asr_nodes)
    assert all(len(n["text"]) <= 500 + 100 for n in asr_nodes)  # chunk target is ~500 chars


def test_load_legacy_hierarchical_format(schema, tmp_path):
    legacy = {
        "graph_id": "legacy-vid",
        "root_event": {"title": "LT", "description": "LD", "key_entities": ["hero"]},
        "super_events": [{"node_id": "super_00", "label": "SL", "sub_macro_ids": ["macro_0000"]}],
        "macro_events": [
            {
                "node_id": "macro_0000",
                "label": "ML",
                "time_range": [0, 10],
                "subevent_ids": ["evt_a"],
                "event_summaries": ["something happens"],
                "key_entity_names": ["hero"],
                "key_entity_types": ["person"],
                "ocr_texts": ["TITLE CARD"],
            }
        ],
        "macro_relations": [{"source_id": "macro_0000", "target_id": "macro_0000", "relation_type": "self"}],
    }
    path = tmp_path / "hierarchical_graph_final.json"
    path.write_text(json.dumps(legacy))

    mem = schema.HierarchicalGraphMemory.load(str(path))
    assert mem.video_key == "legacy-vid"
    assert mem.root.title == "LT"
    assert mem.root.key_entities == [{"name": "hero"}], "bare entity strings must be wrapped as dicts"
    m = mem.macro_events[0]
    assert m.super_id == "super_00", "macro→super linkage must be reconstructed"
    assert m.subgraph is not None
    assert m.subgraph.micro_events[0].event_id == "evt_a"
    assert m.subgraph.entities[0].entity_type == "person"
    assert m.subgraph.on_screen_texts[0].text == "TITLE CARD"
    assert mem.macro_relations[0].type == "self"


# ── embeddings: tokenizer, BM25/cosine search, NPZ round-trip ────────


def test_tokenize_filters_stopwords_and_short_tokens(embeddings):
    assert embeddings._tokenize("The cat is on the mat") == ["cat", "mat"]
    assert embeddings._tokenize("") == []


def _make_index(embeddings, np):
    """A 3-node index with hand-built orthogonal embeddings (no network)."""
    index = embeddings.EmbeddingIndex()
    index.nodes = [
        {"node_id": "n0", "node_type": "entity", "text": "orange cat sitting", "macro_id": "macro_0000"},
        {"node_id": "n1", "node_type": "event", "text": "dog barking loudly", "macro_id": "macro_0001"},
        {"node_id": "n2", "node_type": "on_screen_text", "text": "opening title card", "macro_id": "macro_0002"},
    ]
    index._set_embeddings(np.eye(3, 4, dtype=np.float32))
    index._build_sparse_index()
    return index


def test_search_ranks_matching_node_first(embeddings, monkeypatch):
    np = pytest.importorskip("numpy")
    index = _make_index(embeddings, np)
    # query embedding == n0's embedding → cosine 1.0 for n0; text also matches n0 only
    monkeypatch.setattr(index, "_embed_batch", lambda texts: np.eye(1, 4, dtype=np.float32))
    results = index.search("orange cat", top_k=3)
    assert [r["node_id"] for r in results][0] == "n0"
    assert results[0]["cosine"] == pytest.approx(1.0, abs=1e-4)
    assert results[0]["score"] > results[-1]["score"]


def test_search_filters_by_node_type(embeddings, monkeypatch):
    np = pytest.importorskip("numpy")
    index = _make_index(embeddings, np)
    monkeypatch.setattr(index, "_embed_batch", lambda texts: np.eye(1, 4, dtype=np.float32))
    results = index.search("anything", top_k=10, node_types=["entity"])
    assert results and all(r["node_type"] == "entity" for r in results)
    assert index.search("anything", top_k=10, node_types=["nonexistent_type"]) == []


def test_search_empty_index_returns_empty(embeddings):
    assert embeddings.EmbeddingIndex().search("query") == []


def test_sparse_fallback_omits_zero_score_nodes(embeddings):
    np = pytest.importorskip("numpy")
    index = _make_index(embeddings, np)
    index._dense_disabled = True

    assert [r["node_id"] for r in index.search("orange", top_k=10)] == ["n0"]
    assert index.search("unrelated vocabulary", top_k=10) == []


def test_normalized_rows_are_unit_norm_and_zero_safe(embeddings):
    np = pytest.importorskip("numpy")
    index = embeddings.EmbeddingIndex()
    mat = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)  # includes a zero row
    index._set_embeddings(mat)
    normed = index._normalized()
    assert np.linalg.norm(normed[0]) == pytest.approx(1.0, abs=1e-6)
    assert np.linalg.norm(normed[1]) == 0.0, "zero vector must not produce NaN"


def test_npz_save_load_roundtrip(embeddings, tmp_path):
    np = pytest.importorskip("numpy")
    index = _make_index(embeddings, np)
    rng = np.random.default_rng(42)
    index._set_embeddings(rng.standard_normal((3, 8)).astype(np.float32))

    path = str(tmp_path / "embeddings.npz")
    index.save(path)
    assert os.path.exists(path)

    loaded = embeddings.EmbeddingIndex()
    loaded.load(path)
    assert np.allclose(loaded.embeddings, index.embeddings)
    assert loaded.nodes == index.nodes
    assert loaded._inv_index, "sparse BM25 index must be rebuilt on load"


# ── build_graph: pure helpers (no ffmpeg / LLM / OSS) ────────────────


def test_parse_time_range_variants(build_graph):
    assert build_graph._parse_time_range([]) == [0, 0]
    assert build_graph._parse_time_range([5, 10.5]) == [5.0, 10.5]
    assert build_graph._parse_time_range(["01:00", "01:01:01"]) == [60.0, 3661.0]
    assert build_graph._parse_time_range([None, 3]) == [0, 3.0]


def test_build_asr_dashscope_uses_platform_file_uri(build_graph, tmp_path, monkeypatch):
    source = tmp_path / "音频 # 100%.wav"
    source.write_bytes(b"audio")
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return type(
            "Response",
            (),
            {
                "status_code": 200,
                "output": {"choices": [{"message": {"content": [{"text": "transcript"}]}}]},
            },
        )()

    dashscope = type("DashScope", (), {"MultiModalConversation": type("Conversation", (), {"call": fake_call})})
    monkeypatch.setitem(sys.modules, "dashscope", dashscope)

    assert build_graph._transcribe_chunk_dashscope(str(source), "qwen3-asr-flash", "key") == ["transcript"]
    audio = captured["messages"][0]["content"][0]["audio"]
    assert audio == source.resolve().as_uri()
    assert " " not in audio
    assert "#" not in audio


def _make_macros(build_graph, n):
    return [
        build_graph.MacroEvent(macro_id=f"macro_{i:04d}", label=f"L{i}", time_range=[i * 10.0, (i + 1) * 10.0])
        for i in range(n)
    ]


def test_fallback_aggregation_covers_all_macros(build_graph):
    macros = _make_macros(build_graph, 20)
    agg = build_graph._fallback_aggregation(macros)
    supers = agg["super_events"]
    covered = [mid for se in supers for mid in se["sub_macro_ids"]]
    assert covered == [m.macro_id for m in macros], "every macro must land in exactly one super, in order"
    # each super's time_range spans its own chunk
    assert supers[0]["time_range"][0] == 0.0
    assert supers[-1]["time_range"][1] == 200.0
    assert agg["root"]["title"], "fallback must still synthesize a root"


def test_pipeline_worker_does_not_mark_done_when_phase1_failed(pipeline_worker, monkeypatch, tmp_path):
    (tmp_path / "01_macros.json").write_text("[]")
    monkeypatch.setattr(pipeline_worker, "_p1_alive", lambda _pid: False)

    with pytest.raises(RuntimeError, match="Phase 1 exited without 01_done"):
        pipeline_worker.run_pipeline_worker(
            "video.mp4", str(tmp_path), "model", "key", num_workers=1, poll_interval=0, p1_pid=123
        )

    assert not (tmp_path / "02_done").exists()


def test_embedding_failure_can_resume_from_saved_graph(build_graph, monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    memory = _make_memory(build_graph)

    def fail_build(_self, _nodes):
        raise RuntimeError("synthetic embedding failure")

    monkeypatch.setattr(build_graph.EmbeddingIndex, "build", fail_build)
    with pytest.raises(RuntimeError, match="synthetic embedding failure"):
        build_graph._assemble_save_embed(
            memory.video_key,
            memory.video_path,
            str(tmp_path),
            memory.root,
            memory.super_events,
            memory.macro_events,
            memory.macro_relations,
            memory.super_relations,
        )

    graph_path = tmp_path / "graph_memory.json"
    assert graph_path.exists()
    assert not (tmp_path / "embeddings.npz").exists()

    def build_locally(index, nodes):
        index.nodes = nodes
        index._set_embeddings(np.ones((len(nodes), 3), dtype=np.float32))
        index._build_sparse_index()

    monkeypatch.setattr(build_graph.EmbeddingIndex, "build", build_locally)
    saved = build_graph.HierarchicalGraphMemory.load(str(graph_path))
    build_graph._build_save_embeddings(saved, str(tmp_path))

    loaded = build_graph.EmbeddingIndex()
    loaded.load(str(tmp_path / "embeddings.npz"))
    assert len(loaded.nodes) == loaded.embeddings.shape[0] > 0


def test_merge_asr_into_macros_by_overlap(build_graph):
    macros = _make_macros(build_graph, 2)  # [0,10] and [10,20]
    transcript = [
        {"start_sec": 0.0, "end_sec": 4.0, "text": "hello"},
        {"start_sec": 9.0, "end_sec": 12.0, "text": "bridge"},  # straddles both macros
        {"start_sec": 15.0, "end_sec": 18.0, "text": "goodbye"},
        {"start_sec": 25.0, "end_sec": 30.0, "text": "offscreen"},  # overlaps neither
    ]
    build_graph.merge_asr_into_macros(macros, transcript)
    assert macros[0].asr_text == "hello bridge"
    assert macros[1].asr_text == "bridge goodbye"
    assert "offscreen" not in macros[0].asr_text + macros[1].asr_text


def test_merge_asr_empty_transcript_is_noop(build_graph):
    macros = _make_macros(build_graph, 1)
    build_graph.merge_asr_into_macros(macros, [])
    assert macros[0].asr_text == ""


def test_macro_to_dict_serializes_time_as_str(build_graph):
    m = build_graph.MacroEvent(macro_id="macro_0000", label="L", time_range=[0.0, 3661.0], summary="s")
    d = build_graph._macro_to_dict(m)
    assert d["time_range"] == ["00:00:00", "01:01:01"]
    assert d["macro_id"] == "macro_0000" and d["label"] == "L" and d["summary"] == "s"


def test_parse_window_result_assigns_super_ids(build_graph):
    macros = _make_macros(build_graph, 3)
    agg = {
        "super_events": [
            {"label": "first", "sub_macro_ids": ["macro_0000", "macro_0001"], "time_range": [0, 20]},
            {"label": "second", "sub_macro_ids": ["macro_0002"], "time_range": ["00:20", "00:30"]},
        ],
        "macro_relations": [{"source": "macro_0000", "target": "macro_0002", "type": "leads_to"}],
    }
    supers, rels, counter = build_graph._parse_window_result(agg, macros, super_counter=0)
    assert [s.super_id for s in supers] == ["super_00", "super_01"]
    assert counter == 2, "the counter must advance for the next window"
    assert supers[1].time_range == [20.0, 30.0], "string time ranges must be parsed to seconds"
    # back-links written onto the macros themselves
    assert [m.super_id for m in macros] == ["super_00", "super_00", "super_01"]
    assert rels[0].type == "leads_to" and rels[0].reason == ""
