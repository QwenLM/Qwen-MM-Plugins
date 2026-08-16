"""Hermetic unit tests for the api + search tool handlers.

Understanding tools live in the qwen-mm-plugins-api capability, grouped by model family
(qwen_mm_plugins_api.vl: vision_chat / ocr / grounding; qwen_mm_plugins_api.others: segmentation /
asr — the Omni family in qwen_mm_plugins_api.omni is covered by test_api_omni.py); web +
reverse-image search lives in qwen-mm-plugins-search (qwen_mm_plugins_search.tools +
its pluggable backends). These tools normally hit an external endpoint (DashScope
OpenAI-compatible, Serper/Exa/Tavily, a SAM3 server). The handlers all import their network boundary
lazily *inside* `handle`, so — exactly like test_api_clients.py — monkeypatching the module
attribute (`call_openai_chat`, `backends.search_text`, `serper.post_serper`, `requests.post`) is enough to exercise
the handler with NO live network and NO API key. Nothing here should ever touch the wire.

Split into three layers:
  Tier 1 — pure functions (parsers/formatters): zero mocks.
  Tier 2 — handler guards (empty input, missing key/file/server): early returns, no network.
  Tier 3 — handler happy-path with the network boundary mocked.

Live reachability (does the real API answer?) lives in test_api_reachability.py.
"""

import base64
import json
import os
import types

import pytest

# Collection guard: importing the api/search packages runs build_registry → the `mcp` SDK.
# Without this, a missing `mcp` fails COLLECTION of the whole file instead of skipping.
pytest.importorskip("mcp")

import shared.api_openai as oa  # noqa: E402  (import after the importorskip guard)
from qwen_mm_plugins_api.others import (  # noqa: E402
    asr,
    segmentation,
)
from qwen_mm_plugins_api.vl import (  # noqa: E402
    grounding,
    ocr,
    vision_chat,
)
from qwen_mm_plugins_search import backends as search_backends  # noqa: E402
from qwen_mm_plugins_search import serper  # noqa: E402
from qwen_mm_plugins_search.tools import (  # noqa: E402
    image_search,
    web_extractor,
    web_search,
)


def _is_error(blocks) -> bool:
    return bool(blocks) and blocks[0].get("type") == "text" and blocks[0]["text"].startswith("Error:")


@pytest.mark.parametrize("module", [vision_chat, ocr, grounding])
def test_vl_model_schema_documents_env_override(module):
    description = module.TOOL["args"].model_fields["model"].description
    assert "QWEN_MM_API_VL_MODEL" in description


def _chat_response(content: str):
    """Mimic the OpenAI SDK response shape the handlers read: resp.choices[0].message.content."""
    message = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


@pytest.fixture(autouse=True)
def _default_search_backend(monkeypatch):
    """Keep handler tests independent of a developer's persisted backend selection."""
    monkeypatch.setattr(search_backends, "resolve_backend", lambda: "serper")


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):  # noqa: D401 - test stub
        return None

    def json(self):
        return self._payload


# ══════════════════════════════════════════════════════════════════════
# Tier 1 — pure functions (no network, no mocks)
# ══════════════════════════════════════════════════════════════════════

# ── grounding.parse_grounding ────────────────────────────────────────


def test_grounding_parses_plain_json_and_maps_pixels():
    text = '[{"label": "cat", "bbox_2d": [0, 0, 500, 1000]}]'
    got = grounding.parse_grounding(text, img_w=96, img_h=64)
    assert len(got) == 1
    assert got[0]["label"] == "cat"
    assert got[0]["bbox_normalized"] == [0, 0, 500, 1000]
    # 500/1000*96 = 48 ; 1000/1000*64 = 64
    assert got[0]["bbox_pixel"] == [0, 0, 48, 64]


def test_grounding_strips_markdown_fence():
    text = '```json\n[{"label": "x", "bbox_2d": [100, 100, 200, 200]}]\n```'
    got = grounding.parse_grounding(text, 1000, 1000)
    assert got and got[0]["bbox_pixel"] == [100, 100, 200, 200]


def test_grounding_unwraps_dict_and_alt_keys():
    # dict wrapper + alternate label/bbox key names
    text = '{"detections": [{"name": "dog", "box": [10, 20, 30, 40]}]}'
    got = grounding.parse_grounding(text, 1000, 1000)
    assert got and got[0]["label"] == "dog"
    assert got[0]["bbox_normalized"] == [10, 20, 30, 40]


def test_grounding_ref_box_fallback():
    text = "<ref>bird</ref><box>(10,20),(30,40)</box>"
    got = grounding.parse_grounding(text, 1000, 1000)
    assert got and got[0]["label"] == "bird"
    assert got[0]["bbox_normalized"] == [10, 20, 30, 40]


def test_grounding_garbage_returns_empty():
    assert grounding.parse_grounding("no boxes anywhere", 100, 100) == []


# ── web_search._format_results ───────────────────────────────────────


def test_web_search_format_numbers_and_advances_id():
    docs = [
        {"link": "http://a", "title": "A", "snippet": "sa", "date": "d1"},
        {"link": "http://b", "title": "B", "snippet": "sb"},
    ]
    text, next_id = web_search._format_results(docs, start_id=1)
    assert "[1] http://a" in text and "[2] http://b" in text
    assert next_id == 3


def test_web_search_format_continues_numbering_and_skips_linkless():
    docs = [{"title": "no link"}, {"link": "http://c", "title": "C"}]
    text, next_id = web_search._format_results(docs, start_id=5)
    assert "[5] http://c" in text  # linkless doc skipped, numbering stays contiguous
    assert next_id == 6


def test_web_search_format_empty():
    text, next_id = web_search._format_results([], start_id=1)
    assert text == "No results found." and next_id == 1


# ── image_search._format_results ─────────────────────────────────────


def test_image_search_format_and_empty():
    docs = [{"title": "T", "link": "http://l", "source": "src", "imageUrl": "http://img"}]
    out = image_search._format_results(docs)
    assert "[1]" in out and "http://img" in out and "http://l" in out
    assert image_search._format_results([]) == "No results found."


def test_image_search_crop_converts_rgba_to_jpeg(tmp_path):
    from PIL import Image

    source = tmp_path / "rgba.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(source)

    cropped_path = image_search._crop_bbox(str(source), [0, 0, 500, 1000])
    try:
        with Image.open(cropped_path) as cropped:
            assert cropped.format == "JPEG"
            assert cropped.mode == "RGB"
            assert cropped.size == (10, 10)
    finally:
        os.unlink(cropped_path)


# ── asr time/format helpers ──────────────────────────────────────────


def test_asr_ms_to_srt_time():
    assert asr._ms_to_srt_time(3_661_001) == "01:01:01,001"
    assert asr._ms_to_srt_time(0) == "00:00:00,000"


def test_asr_format_srt_and_text():
    chunks = [(0.0, 1.5, ["hello"]), (1.5, 3.0, ["world", "again"])]
    srt = asr._format_srt(chunks)
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "hello" in srt and "world" in srt
    # three sentences → numbered 1,2,3
    assert srt.splitlines()[0] == "1"
    assert asr._format_text(chunks) == "hello\nworld\nagain"


# ══════════════════════════════════════════════════════════════════════
# Tier 2 — handler guards (early returns, no network)
# ══════════════════════════════════════════════════════════════════════


def test_web_search_empty_queries_guard():
    assert _is_error(web_search.handle({"queries": []}))


def test_web_extractor_empty_urls_guard():
    assert _is_error(web_extractor.handle({"urls": []}))


def test_web_search_missing_key_guard(monkeypatch):
    monkeypatch.setattr(search_backends, "resolve_api_key", lambda *_a: "")
    blocks = web_search.handle({"queries": ["hello"]})
    assert _is_error(blocks) and "API key" in blocks[0]["text"]


def test_image_search_missing_key_guard(monkeypatch):
    monkeypatch.setattr(
        search_backends,
        "resolve_backend",
        lambda: pytest.fail("image_search must not consult the text-search backend"),
    )
    monkeypatch.setattr(serper, "resolve_serper_key", lambda a: "")
    blocks = image_search.handle({"image_path": "/nope.jpg"})
    assert _is_error(blocks) and "SERPER_API_KEY" in blocks[0]["text"]


def test_segmentation_missing_server_guard(monkeypatch):
    # Force the SAM3 env lookup to miss so the guard fires regardless of the ambient env.
    monkeypatch.setattr(segmentation, "get_env", lambda *_a, **_k: None)
    blocks = segmentation.handle({"image_path": "/x.jpg", "prompt": "cat"})
    assert _is_error(blocks) and "SAM3" in blocks[0]["text"]


def test_ocr_missing_file_guard():
    assert _is_error(ocr.handle({"image_path": "/does/not/exist.png"}))


def test_grounding_missing_file_guard():
    assert _is_error(grounding.handle({"image_path": "/does/not/exist.png", "prompt": "cat"}))


def test_asr_missing_file_guard():
    # transcribe_audio is local-only (no OSS fallback): a missing file returns an error, not a crash.
    blocks = asr.handle({"file_path": "/does/not/exist.wav"})
    assert _is_error(blocks) and "file not found" in blocks[0]["text"]


def test_vision_chat_dry_run_builds_request_without_network(sample_image):
    blocks = vision_chat.handle(
        {
            "images": [sample_image],
            "text": "hello",
            "dry_run": True,
            "vl_high_resolution_images": True,
        }
    )
    assert len(blocks) == 1 and blocks[0]["type"] == "text"
    payload = json.loads(blocks[0]["text"])
    assert payload["request"]["extra_body"] == {"vl_high_resolution_images": True}
    assert "optional_extra_body" not in payload["request"]
    content = payload["request"]["messages"][0]["content"]
    # image part first, text part last; base64 image redacted (not the raw data URL)
    assert content[-1] == {"type": "text", "text": "hello"}
    img_part = content[0]
    assert img_part["type"] == "image_url"
    assert img_part["image_url"]["url"].startswith("<base64 image")


def test_vision_chat_model_precedence(monkeypatch):
    monkeypatch.setenv("QWEN_MM_API_VL_MODEL", "env-vl")
    env_payload = json.loads(vision_chat.handle({"dry_run": True})[0]["text"])
    explicit_payload = json.loads(vision_chat.handle({"model": "explicit-vl", "dry_run": True})[0]["text"])
    assert env_payload["request"]["model"] == "env-vl"
    assert explicit_payload["request"]["model"] == "explicit-vl"


def test_encode_video_source_uploads_to_oss_when_configured(monkeypatch):
    """Unified OSS trigger: a local video is uploaded and passed by URL when OSS is configured —
    no local frame extraction (so this needs neither ffmpeg nor a real file)."""
    from shared import oss

    monkeypatch.setattr(oss, "is_upload_configured", lambda: True)
    monkeypatch.setattr(oss, "upload_and_sign", lambda path, **kw: f"https://oss.example/{os.path.basename(path)}?sig")
    part = oa.encode_video_source("/some/local/clip.mp4")
    assert part == {"type": "video_url", "video_url": {"url": "https://oss.example/clip.mp4?sig"}}


def test_encode_video_source_allow_upload_false_skips_oss(monkeypatch, sample_video):
    """dry_run passes allow_upload=False, so even with OSS configured it must NOT upload — it falls
    back to the local frame path."""
    from shared import oss

    monkeypatch.setattr(oss, "is_upload_configured", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("upload_and_sign must not be called when allow_upload=False")

    monkeypatch.setattr(oss, "upload_and_sign", _boom)
    part = oa.encode_video_source(sample_video, allow_upload=False)
    assert part["type"] == "video"  # frame-list form, uploaded nothing


def test_vision_chat_dry_run_does_not_upload(monkeypatch, sample_video):
    """A dry_run preview never hits the network, even with OSS configured; it notes the real behavior."""
    from shared import oss

    monkeypatch.setattr(oss, "is_upload_configured", lambda: True)
    monkeypatch.setattr(oss, "upload_and_sign", lambda *a, **k: (_ for _ in ()).throw(AssertionError("uploaded")))
    blocks = vision_chat.handle({"videos": [sample_video], "text": "hi", "dry_run": True})
    payload = json.loads(blocks[0]["text"])
    assert "OSS is configured" in payload.get("note", "")


# ── Server-side video-duration cap → degrade to local frame sampling ──


def test_vl_video_max_sec_resolves():
    assert oa.vl_video_max_sec("qwen3.7-plus") == 2 * 3600
    assert oa.vl_video_max_sec("qwen-vl-max-latest") == 20 * 60  # prefix match
    assert oa.vl_video_max_sec("some-unknown-model") is None
    assert oa.vl_video_max_sec(None) is None


def test_omni_video_max_sec_resolves():
    from shared import api_omni

    assert api_omni.omni_video_max_sec("qwen3.5-omni-plus") == 3600
    assert api_omni.omni_video_max_sec("qwen-omni-turbo") == 180
    assert api_omni.omni_video_max_sec("mystery") is None


def test_video_duration_exceeds_predicate(monkeypatch):
    from shared import video

    monkeypatch.setattr(video, "probe_media", lambda p: {"format": {"duration": "5400"}})  # 90 min
    assert video.video_duration_exceeds("/x/clip.mp4", 3600) is True  # 90 min > 1 h
    assert video.video_duration_exceeds("/x/clip.mp4", 7200) is False  # 90 min < 2 h
    assert video.video_duration_exceeds("/x/clip.mp4", None) is False  # unknown cap
    assert video.video_duration_exceeds("https://h/clip.mp4", 60) is False  # remote: can't probe


def test_encode_video_source_over_limit_degrades_to_frames(monkeypatch, sample_video):
    """With OSS configured, an over-limit local video is NOT uploaded — it degrades to local frames."""
    from shared import oss, video

    monkeypatch.setattr(oss, "is_upload_configured", lambda: True)
    monkeypatch.setattr(oss, "upload_and_sign", lambda *a, **k: (_ for _ in ()).throw(AssertionError("uploaded")))
    monkeypatch.setattr(video, "video_duration_exceeds", lambda p, cap: True)  # pretend it's over the cap
    part = oa.encode_video_source(sample_video, model="qwen-vl-max")  # 20-min cap
    assert part["type"] == "video"  # frame-list form, uploaded nothing


def test_omni_over_limit_degrades_to_frames_and_audio(monkeypatch):
    """Omni: an over-cap local video degrades UP FRONT to frames + audio (no transcode / no upload),
    regardless of OSS. Within the cap, the normal delivery path runs."""
    from qwen_mm_plugins_api.omni import _common
    from shared import oss, video

    monkeypatch.setattr(_common, "_frames_and_audio_parts", lambda *a, **k: [{"type": "video", "video": ["f0", "f1"]}])

    # Over the cap → frames + audio, whether or not OSS is configured, without touching _preprocess_video.
    def _boom_preprocess(*a, **k):
        raise AssertionError("must not preprocess an over-cap video")

    monkeypatch.setattr(_common, "_preprocess_video", _boom_preprocess)
    monkeypatch.setattr(video, "video_duration_exceeds", lambda p, cap: True)
    for configured in (True, False):
        monkeypatch.setattr(oss, "is_upload_configured", lambda c=configured: c)
        parts = _common._local_video_parts("/x/clip.mp4", 1.0, 200704, [], 3600, "qwen3.5-omni-plus")
        assert parts == [{"type": "video", "video": ["f0", "f1"]}]


# ══════════════════════════════════════════════════════════════════════
# Tier 3 — happy path with the network boundary mocked
# ══════════════════════════════════════════════════════════════════════


def test_ocr_returns_model_text(monkeypatch, sample_image):
    pytest.importorskip("openai")
    monkeypatch.setattr(oa, "call_openai_chat", lambda **kw: _chat_response("EXTRACTED TEXT"))
    blocks = ocr.handle({"image_path": sample_image})
    assert blocks == [{"type": "text", "text": "EXTRACTED TEXT"}]


def test_vision_chat_passes_high_resolution_as_optional_hint(monkeypatch, sample_image):
    pytest.importorskip("openai")
    request = {}

    def fake_call(**kwargs):
        request.update(kwargs)
        return types.SimpleNamespace(model_dump=lambda: {"choices": []})

    monkeypatch.setattr(oa, "call_openai_chat", fake_call)
    blocks = vision_chat.handle(
        {
            "images": [sample_image],
            "text": "hello",
            "vl_high_resolution_images": True,
        }
    )

    assert not _is_error(blocks)
    assert request["optional_extra_body"] == {"vl_high_resolution_images": True}
    assert "extra_body" not in request


def test_grounding_maps_boxes_and_draws(monkeypatch, sample_image):
    pytest.importorskip("openai")
    # sample_image is 96×64; a right-half box in 0-1000 coords → pixels [48,0,96,64]
    model_json = '[{"label": "blue", "bbox_2d": [500, 0, 1000, 1000]}]'
    request = {}

    def fake_call(**kwargs):
        request.update(kwargs)
        return _chat_response(model_json)

    monkeypatch.setattr(oa, "call_openai_chat", fake_call)

    blocks = grounding.handle({"image_path": sample_image, "prompt": "regions", "return_img": True})
    result = json.loads(blocks[0]["text"])
    assert request["optional_extra_body"] == {"enable_thinking": False}
    assert "extra_body" not in request
    assert result["image_size"] == {"width": 96, "height": 64}
    assert result["detections"][0]["bbox_pixel"] == [48, 0, 96, 64]
    # return_img=True with a detection → an image block is appended
    assert any(b["type"] == "image" for b in blocks)


def test_web_search_formats_serper_docs(monkeypatch):
    monkeypatch.setattr(search_backends, "resolve_api_key", lambda *_a: "k")
    monkeypatch.setattr(
        serper,
        "post_serper",
        lambda path, payload, key, **kw: {"organic": [{"link": "http://x", "title": "T", "snippet": "S"}]},
    )
    blocks = web_search.handle({"queries": ["q"]})
    text = blocks[0]["text"]
    assert "[1] http://x" in text and "web_extractor" in text  # result + tip


def test_web_search_multi_query_headers_and_numbering(monkeypatch):
    monkeypatch.setattr(search_backends, "resolve_api_key", lambda *_a: "k")
    seq = iter(
        [
            {"organic": [{"link": "http://a", "title": "A"}]},
            {"organic": [{"link": "http://b", "title": "B"}]},
        ]
    )
    monkeypatch.setattr(serper, "post_serper", lambda *a, **k: next(seq))
    text = web_search.handle({"queries": ["q1", "q2"]})[0]["text"]
    assert "## Query: q1" in text and "## Query: q2" in text
    assert "[1] http://a" in text and "[2] http://b" in text  # numbering continues across queries


def test_web_extractor_truncates_and_labels_goal(monkeypatch):
    monkeypatch.setattr(search_backends, "resolve_api_key", lambda *_a: "k")
    monkeypatch.setattr(serper, "post_serper", lambda *a, **k: {"markdown": "M" * 20_000})
    blocks = web_extractor.handle({"urls": ["http://p"], "goal": "find X"})
    text = blocks[0]["text"]
    assert "## http://p" in text and "Goal: find X" in text
    body = text.split("\n\n", 2)[-1]
    assert len(body) <= web_extractor.CONTENT_LIMIT


def test_image_search_ignores_text_backend_and_uses_serper(monkeypatch):
    monkeypatch.setattr(
        search_backends,
        "resolve_backend",
        lambda: pytest.fail("image_search must not consult the text-search backend"),
    )
    monkeypatch.setattr(serper, "resolve_serper_key", lambda _a: "serper-key")
    monkeypatch.setattr(
        serper,
        "post_serper",
        lambda *_a, **_k: {
            "organic": [{"title": "match", "link": "https://example.com/match", "imageUrl": "https://img"}]
        },
    )

    blocks = image_search.handle({"image_path": "https://example.com/source.jpg"})

    assert not _is_error(blocks)
    assert "https://example.com/match" in blocks[0]["text"]


def test_image_search_url_fast_path(monkeypatch):
    monkeypatch.setattr(serper, "resolve_serper_key", lambda a: "k")
    monkeypatch.setattr(
        serper, "post_serper", lambda *a, **k: {"organic": [{"title": "T", "link": "http://l", "imageUrl": "http://i"}]}
    )
    # a public URL and no crop → straight to Lens, no upload/download
    blocks = image_search.handle({"image_path": "https://example.com/a.jpg"})
    assert "[1]" in blocks[0]["text"] and "http://i" in blocks[0]["text"]


def test_image_search_local_file_requires_public_upload_consent(monkeypatch, sample_image):
    monkeypatch.setattr(serper, "resolve_serper_key", lambda a: "k")
    monkeypatch.setattr(
        image_search,
        "_upload_public",
        lambda *_a, **_k: pytest.fail("must not upload without explicit consent"),
    )

    blocks = image_search.handle({"image_path": sample_image})

    assert _is_error(blocks)
    assert "allow_public_upload=true" in blocks[0]["text"]


def test_image_search_local_file_uploads_after_consent(monkeypatch, sample_image):
    uploaded = []
    monkeypatch.setattr(serper, "resolve_serper_key", lambda a: "k")
    monkeypatch.setattr(image_search, "_upload_public", lambda path: uploaded.append(path) or "https://uguu.se/a.jpg")
    monkeypatch.setattr(
        image_search,
        "_lens_search",
        lambda url, key: [{"title": "T", "link": "https://example.com/result"}],
    )

    blocks = image_search.handle({"image_path": sample_image, "allow_public_upload": True})

    assert uploaded == [sample_image]
    assert "https://example.com/result" in blocks[0]["text"]


def test_image_search_crop_failure_never_uploads_original(monkeypatch, sample_image):
    monkeypatch.setattr(serper, "resolve_serper_key", lambda a: "k")
    monkeypatch.setattr(image_search, "_crop_bbox", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad crop")))
    monkeypatch.setattr(
        image_search,
        "_upload_public",
        lambda *_a, **_k: pytest.fail("must not upload the original after crop failure"),
    )

    blocks = image_search.handle(
        {
            "image_path": sample_image,
            "bbox": [0, 0, 500, 1000],
            "allow_public_upload": True,
        }
    )

    assert _is_error(blocks)
    assert "could not crop image: bad crop" in blocks[0]["text"]


def test_segmentation_returns_text_and_image(monkeypatch):
    import requests

    vis_b64 = base64.b64encode(b"fake-png-bytes").decode()
    payload = {"num_masks": 1, "results": [{"score": 0.912, "box": [1.11, 2.22, 3.33, 4.44]}], "image_b64": vis_b64}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeHTTPResponse(payload))

    blocks = segmentation.handle({"image_path": __file__, "prompt": "thing", "server": "http://sam3"})
    assert blocks[0]["type"] == "text"
    result = json.loads(blocks[0]["text"])
    assert result["num_masks"] == 1 and result["results"][0]["score"] == 0.912
    assert any(b["type"] == "image" for b in blocks)


def test_segmentation_no_masks_omits_image(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeHTTPResponse({"num_masks": 0, "results": []}))
    blocks = segmentation.handle({"image_path": __file__, "prompt": "thing", "server": "http://sam3"})
    assert all(b["type"] != "image" for b in blocks)


def test_segmentation_connection_error_is_reported(monkeypatch):
    import requests

    def _boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", _boom)
    blocks = segmentation.handle({"image_path": __file__, "prompt": "thing", "server": "http://sam3"})
    assert _is_error(blocks) and "cannot connect" in blocks[0]["text"]


# ── Out-of-box error handling: API/network failures return a clean text_error, never raise ──
# (regression guards for the try/except added to ocr / grounding / segmentation)
def test_ocr_returns_error_when_api_raises(monkeypatch, sample_image):
    def _boom(**kw):
        raise RuntimeError("boom-401")

    monkeypatch.setattr(oa, "call_openai_chat", _boom)
    out = ocr.handle({"image_path": sample_image})
    assert _is_error(out) and "boom-401" in out[0]["text"]


def test_grounding_returns_error_when_api_raises(monkeypatch, sample_image):
    def _boom(**kw):
        raise RuntimeError("boom-timeout")

    monkeypatch.setattr(oa, "call_openai_chat", _boom)
    out = grounding.handle({"image_path": sample_image, "prompt": "cats"})
    assert _is_error(out) and "boom-timeout" in out[0]["text"]


def test_segmentation_returns_error_on_non_connection_failure(monkeypatch, sample_image):
    import requests

    def _boom(*a, **k):
        raise requests.exceptions.Timeout("slow")  # not ConnectionError -> hits the generic except

    monkeypatch.setattr(requests, "post", _boom)
    out = segmentation.handle({"image_path": sample_image, "server": "http://sam3.invalid"})
    assert _is_error(out)
