"""Tests for the Omni audio/video atomic tools (the api ``omni/`` subpackage).

The Omni tools live in the qwen-mm-plugins-api capability (``qwen_mm_plugins_api.omni``) alongside the
VL and other cloud tools. conftest auto-discovers the api server package (it scans src/capabilities/*/
for the server package), so it imports like any other server. Offline tests cover the registry, the
dry_run request shape, and output formatting (with the model boundary mocked); a reachability-marked
test hits the real Omni endpoint only when DASHSCOPE_API_KEY is set.
"""

import base64
import json
import os
import tempfile
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")  # importing the server pulls in the mcp SDK

import qwen_mm_plugins_api as oav
from qwen_mm_plugins_api.omni import _common
from shared import api_omni
from shared.env import get_env

_EXPECTED = {
    "omni_asr",
    "omni_asr_timestamped",
    "omni_multi_speaker_asr",
    "omni_av_caption",
    "omni_av_grounding",
    "omni_av_counting",
    "omni_music_caption",
}


def _dummy(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _preview(blocks) -> dict:
    assert blocks[0]["type"] == "text"
    assert "DRY RUN" in blocks[0]["text"]
    return json.loads(blocks[0]["text"].split("\n", 1)[1])


# ── Registry ───────────────────────────────────────────────────────────────────────────────────
def test_lists_the_tools():
    # The api server also serves the VL and other tools; assert the Omni family is registered here.
    assert _EXPECTED <= {t["name"] for t in oav.list_tools()}


def test_every_tool_has_schema_and_handler():
    for name in _EXPECTED:
        tool = next(t for t in oav.list_tools() if t["name"] == name)
        assert tool["inputSchema"]["type"] == "object"
        assert "QWEN_MM_API_OMNI_MODEL" in tool["inputSchema"]["properties"]["model"]["description"]
        assert callable(oav.get_handler(name))


# ── dry_run: request shape, no network, no file read ─────────────────────────────────────────────
def test_asr_dry_run_sends_audio_and_streaming_flags():
    path = _dummy(".wav")
    try:
        prev = _preview(oav.get_handler("omni_asr")({"file_path": path, "dry_run": True}))
    finally:
        os.remove(path)
    assert prev["stream"] is True
    assert prev["modalities"] == ["text"]
    assert prev["messages"][0]["content"][0]["type"] == "input_audio"


def test_omni_model_precedence(monkeypatch):
    monkeypatch.setenv("QWEN_MM_API_OMNI_MODEL", "env-omni")
    env_preview = _preview(oav.get_handler("omni_asr")({"file_path": "https://example.com/audio.mp3", "dry_run": True}))
    explicit_preview = _preview(
        oav.get_handler("omni_asr")(
            {"file_path": "https://example.com/audio.mp3", "model": "explicit-omni", "dry_run": True}
        )
    )
    assert env_preview["model"] == "env-omni"
    assert explicit_preview["model"] == "explicit-omni"


def test_call_omni_resolves_env_default(monkeypatch):
    import openai

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        choice = SimpleNamespace(delta=SimpleNamespace(content="ok"))
        return [SimpleNamespace(usage=None, choices=[choice])]

    def make_client(**kwargs):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setenv("QWEN_MM_API_OMNI_MODEL", "env-omni")
    monkeypatch.setattr(openai, "OpenAI", make_client)
    text, _ = api_omni.call_omni(base_url="http://local/v1", api_key="key", messages=[])
    assert text == "ok"
    assert captured["model"] == "env-omni"


def test_call_omni_json_resolves_env_default(monkeypatch):
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return '{"ok": true}', None

    monkeypatch.setenv("QWEN_MM_API_OMNI_MODEL", "env-omni")
    monkeypatch.setattr(api_omni, "call_omni", fake_call)
    assert api_omni.call_omni_json(base_url="http://local/v1", api_key="key", messages=[]) == {"ok": True}
    assert captured["model"] == "env-omni"


def test_caption_dry_run_sends_video_with_sampling_knobs():
    path = _dummy(".mp4")
    try:
        prev = _preview(oav.get_handler("omni_av_caption")({"file_path": path, "fps": 2, "dry_run": True}))
    finally:
        os.remove(path)
    part = prev["messages"][0]["content"][0]
    assert part["type"] == "video_url"
    assert part["fps"] == 2
    assert "max_pixels" in part


def test_grounding_and_counting_put_query_in_prompt():
    vid = _dummy(".mp4")
    try:
        gp = _preview(oav.get_handler("omni_av_grounding")({"file_path": vid, "query": "the goal", "dry_run": True}))
        cp = _preview(oav.get_handler("omni_av_counting")({"file_path": vid, "target": "cars", "dry_run": True}))
    finally:
        os.remove(vid)
    assert "the goal" in gp["messages"][0]["content"][1]["text"]
    assert "cars" in cp["messages"][0]["content"][1]["text"]


# ── Output formatting with the model boundary mocked ─────────────────────────────────────────────
def _mock_json(monkeypatch, payload):
    monkeypatch.setattr(_common, "call_omni_json", lambda **kw: payload)


def test_music_caption_dry_run_sends_audio(monkeypatch):
    path = _dummy(".mp3")
    try:
        prev = _preview(oav.get_handler("omni_music_caption")({"file_path": path, "dry_run": True}))
    finally:
        os.remove(path)
    assert prev["modalities"] == ["text"]
    assert prev["messages"][0]["content"][0]["type"] == "input_audio"


def test_music_caption_formats_tags_and_caption(monkeypatch):
    payload = {
        "genre": ["electronic", "synthwave"],
        "moods": ["nostalgic", "dreamy"],
        "instruments": ["synthesizer", "drum machine"],
        "has_vocals": False,
        "vocal_language": "none",
        "vocal_gender": "none",
        "vocal_timbre": [],
        "key": "a minor",
        "time_signature": "4/4",
        "caption": "A dreamy synthwave instrumental in A minor.",
    }
    _mock_json(monkeypatch, payload)
    path = _dummy(".mp3")
    try:
        blocks = oav.get_handler("omni_music_caption")({"file_path": path})
    finally:
        os.remove(path)
    result = json.loads(blocks[0]["text"])
    assert result["genre"] == ["electronic", "synthwave"]
    assert result["time_signature"] == "4/4" and result["has_vocals"] is False
    assert blocks[1]["text"] == payload["caption"]  # summary = the dense caption


def test_asr_formats_plain_text(monkeypatch):
    _mock_json(monkeypatch, {"text": "hello world"})
    path = _dummy(".wav")
    try:
        blocks = oav.get_handler("omni_asr")({"file_path": path})
    finally:
        os.remove(path)
    assert json.loads(blocks[0]["text"]) == {"text": "hello world"}
    assert blocks[1]["text"] == "hello world"


def test_timestamped_emits_segments_and_srt(monkeypatch):
    _mock_json(monkeypatch, {"segments": [{"start": 0, "end": 1.5, "text": "hi"}]})
    path = _dummy(".wav")
    try:
        blocks = oav.get_handler("omni_asr_timestamped")({"file_path": path})
    finally:
        os.remove(path)
    result = json.loads(blocks[0]["text"])
    assert result["granularity"] == "sentence"
    assert result["segments"][0]["text"] == "hi"
    assert "00:00:00,000 --> 00:00:01,500" in blocks[1]["text"]  # SRT rendering


def test_multi_speaker_labels_speakers_in_srt(monkeypatch):
    _mock_json(
        monkeypatch,
        {
            "segments": [
                {"speaker": "Speaker 1", "start": 0, "end": 1, "text": "hi"},
                {"speaker": "Speaker 2", "start": 1, "end": 2, "text": "bye"},
            ]
        },
    )
    path = _dummy(".wav")
    try:
        blocks = oav.get_handler("omni_multi_speaker_asr")({"file_path": path})
    finally:
        os.remove(path)
    result = json.loads(blocks[0]["text"])
    assert result["speakers"] == ["Speaker 1", "Speaker 2"]
    assert "[Speaker 1] hi" in blocks[1]["text"]


def test_string_timestamps_are_coerced_to_float(monkeypatch):
    # the model sometimes emits start/end as strings ("0.0", "00:03") — normalize_times → numbers
    _mock_json(monkeypatch, {"matches": [{"start": "0.0", "end": "00:03", "reason": "a cat"}]})
    path = _dummy(".mp4")
    try:
        blocks = oav.get_handler("omni_av_grounding")({"file_path": path, "query": "a cat"})
    finally:
        os.remove(path)
    seg = json.loads(blocks[0]["text"])["matches"][0]
    assert seg["start"] == 0.0 and isinstance(seg["start"], float)
    assert seg["end"] == 3.0 and isinstance(seg["end"], float)


def test_caption_returns_markdown_report(monkeypatch):
    # caption is the one report-style tool: raw Markdown from call_omni, no JSON parse
    report = "## Storyline\n\n<0:00.000> - <0:03.000>\nA test pattern.\n\n## Summary of Safety Findings\n\nSafe."
    monkeypatch.setattr(_common, "call_omni", lambda **kw: (report, None))
    path = _dummy(".mp4")
    try:
        blocks = oav.get_handler("omni_av_caption")({"file_path": path})
    finally:
        os.remove(path)
    assert blocks[0]["text"] == report


def test_grounding_wraps_matches(monkeypatch):
    _mock_json(monkeypatch, {"matches": [{"start": 3, "end": 5, "score": 0.9, "reason": "goal"}]})
    path = _dummy(".mp4")
    try:
        blocks = oav.get_handler("omni_av_grounding")({"file_path": path, "query": "the goal"})
    finally:
        os.remove(path)
    result = json.loads(blocks[0]["text"])
    assert result["query"] == "the goal"
    assert result["matches"][0]["start"] == 3


def test_counting_totals_and_respects_top_k(monkeypatch):
    _mock_json(monkeypatch, {"count": 2, "occurrences": [{"start": 1, "end": 1}, {"start": 4, "end": 4}]})
    path = _dummy(".mp4")
    try:
        blocks = oav.get_handler("omni_av_counting")({"file_path": path, "target": "cars"})
    finally:
        os.remove(path)
    result = json.loads(blocks[0]["text"])
    assert result["count"] == 2 and result["target"] == "cars"


def test_counting_derives_count_from_occurrences(monkeypatch):
    # model omitted "count" — it should fall back to len(occurrences)
    _mock_json(monkeypatch, {"occurrences": [{"start": 1, "end": 1}]})
    path = _dummy(".mp4")
    try:
        blocks = oav.get_handler("omni_av_counting")({"file_path": path, "target": "x"})
    finally:
        os.remove(path)
    assert json.loads(blocks[0]["text"])["count"] == 1


def test_missing_file_returns_error():
    blocks = oav.get_handler("omni_asr")({"file_path": "/no/such/file.wav"})
    assert blocks[0]["type"] == "text" and blocks[0]["text"].startswith("Error")


# ── Local-video preprocessing (smart_resize frames + 16 kHz mono audio + inline size budget) ────
def test_local_video_is_preprocessed_before_upload(monkeypatch):
    calls = {}

    def fake_preprocess(file_path, out_path, max_pixels, **kw):
        calls["args"] = (file_path, max_pixels)
        calls["fps"] = kw.get("fps")
        with open(out_path, "wb") as f:
            f.write(b"TRANSCODED")

    sent = {}

    def fake_call(**kw):
        sent["messages"] = kw["messages"]
        return ("## Storyline\n\nok", None)

    monkeypatch.setattr(_common, "_preprocess_video", fake_preprocess)
    monkeypatch.setattr(_common, "call_omni", fake_call)
    path = _dummy(".mp4")
    try:
        oav.get_handler("omni_av_caption")({"file_path": path, "max_pixels": 123456, "fps": 3})
    finally:
        os.remove(path)
    assert calls["args"] == (path, 123456)
    assert calls["fps"] == 3  # the sampling fps caps the encoded frame rate too
    part = sent["messages"][0]["content"][0]
    assert part["type"] == "video_url"
    # the request carries the TRANSCODED temp file, not the original
    import base64

    assert base64.b64encode(b"TRANSCODED").decode() in part["video_url"]["url"]


def test_preprocess_failure_falls_back_to_original_file(monkeypatch):
    def fake_preprocess(file_path, out_path, max_pixels, **kw):
        raise RuntimeError("ffmpeg exploded")

    sent = {}

    def fake_call(**kw):
        sent["messages"] = kw["messages"]
        return ("## Storyline\n\nok", None)

    monkeypatch.setattr(_common, "_preprocess_video", fake_preprocess)
    monkeypatch.setattr(_common, "call_omni", fake_call)
    path = _dummy(".mp4")
    try:
        with open(path, "wb") as f:
            f.write(b"ORIGINAL")
        blocks = oav.get_handler("omni_av_caption")({"file_path": path})
    finally:
        os.remove(path)
    assert not blocks[0]["text"].startswith("Error")
    import base64

    part = sent["messages"][0]["content"][0]
    assert base64.b64encode(b"ORIGINAL").decode() in part["video_url"]["url"]


def test_oversized_original_is_not_sent_when_preprocess_fails(monkeypatch):
    # the fallback only applies when the original already fits: an over-limit file must surface the
    # actionable error rather than trade it for the endpoint's rejection
    def fake_preprocess(file_path, out_path, max_pixels, **kw):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(_common, "_preprocess_video", fake_preprocess)
    monkeypatch.setattr(_common, "OMNI_MAX_UPLOAD_BYTES", 4)
    monkeypatch.setattr(_common, "call_omni", lambda **kw: pytest.fail("must not call the endpoint"))
    path = _dummy(".mp4")
    try:
        with open(path, "wb") as f:
            f.write(b"WAY TOO BIG")
        blocks = oav.get_handler("omni_av_caption")({"file_path": path})
    finally:
        os.remove(path)
    assert blocks[0]["text"].startswith("Error") and "ffmpeg exploded" in blocks[0]["text"]


def test_preprocess_video_resizes_and_downmixes_audio(sample_media_av, tmp_path):
    # real ffmpeg round-trip on the 160×120 AV fixture: dims snap to the 32-px patch grid within
    # the max_pixels budget, and the audio track comes out 16 kHz mono
    from shared.video import probe_media

    out = str(tmp_path / "pre.mp4")
    _common._preprocess_video(sample_media_av, out, 200704)
    streams = {s["codec_type"]: s for s in probe_media(out)["streams"]}
    v, a = streams["video"], streams["audio"]
    assert v["width"] % 32 == 0 and v["height"] % 32 == 0
    assert v["width"] * v["height"] <= 200704
    assert int(a["sample_rate"]) == 16000 and int(a["channels"]) == 1


def test_preprocess_video_lands_inside_the_byte_budget(sample_media_av, tmp_path):
    # real ffmpeg round-trip against a deliberately tight budget: the closed loop must deliver a
    # file under it, since the whole point is that Omni rejects an over-limit content item
    out = str(tmp_path / "budget.mp4")
    _common._preprocess_video(sample_media_av, out, 200704, fps=1.0, max_bytes=50_000)
    assert 0 < os.path.getsize(out) <= 50_000


def test_preprocess_video_caps_frame_rate_at_the_sampling_fps(sample_media_av, tmp_path):
    # the fixture is 10 fps; encoding above the fps Omni samples at is pure payload
    from shared.video import get_video_info

    out = str(tmp_path / "rate.mp4")
    _common._preprocess_video(sample_media_av, out, 200704, fps=1.0)
    assert get_video_info(out)["native_fps"] <= 1.5


def test_preprocess_video_rejects_a_video_too_long_to_fit(sample_media_av, tmp_path):
    # a budget so tight that the 3s fixture would need less than _MIN_VIDEO_KBPS: fail loudly with
    # the escape hatches instead of shipping an over-limit request
    out = str(tmp_path / "toolong.mp4")
    with pytest.raises(RuntimeError, match="too long"):
        _common._preprocess_video(sample_media_av, out, 200704, fps=1.0, max_bytes=20_000)


# ── The inline cap: 10 MB of base64, enforced before the request leaves ────────────────────────
def test_upload_budget_is_derived_from_the_10mb_base64_cap():
    # the endpoint measures the ENCODED string, so the file budget must be 3/4 of the cap (+ margin);
    # getting this wrong by 2x is what made every transcoded video get rejected
    assert api_omni.OMNI_MAX_B64_BYTES == 10 * 1000 * 1000
    assert api_omni.b64_len(api_omni.OMNI_MAX_UPLOAD_BYTES) <= api_omni.OMNI_MAX_B64_BYTES
    assert api_omni.OMNI_MAX_UPLOAD_BYTES > api_omni.OMNI_MAX_B64_BYTES * 0.7  # not needlessly tight


def test_data_url_refuses_a_file_over_the_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(api_omni, "OMNI_MAX_B64_BYTES", 8)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"0123456789")
    with pytest.raises(api_omni.PayloadTooLargeError, match="base64"):
        api_omni.omni_video_part(str(big))


def test_call_omni_gates_the_total_inline_payload(monkeypatch):
    # the per-item check cannot see a frames+audio pair adding up, so call_omni sums them all
    monkeypatch.setattr(api_omni, "OMNI_MAX_B64_BYTES", 100)
    messages = [
        {
            "role": "user",
            "content": [
                api_omni.omni_frames_part([api_omni.jpeg_data_url("A" * 60), api_omni.jpeg_data_url("B" * 60)]),
                {"type": "text", "text": "hi"},
            ],
        }
    ]
    assert api_omni.inline_b64_bytes(messages) == 120
    with pytest.raises(api_omni.PayloadTooLargeError, match="inline base64"):
        api_omni.call_omni(base_url="http://x/v1", api_key="k", messages=messages)


def test_audio_data_url_omits_the_mime_prefix(tmp_path):
    # DashScope documents the bare `data:;base64,` form for input_audio; `format` carries the type
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    part = api_omni.omni_audio_part(str(wav))
    assert part["input_audio"]["data"].startswith("data:;base64,")
    assert part["input_audio"]["format"] == "wav"


def test_audio_raw_b64_optin_strips_the_wrapper(tmp_path, monkeypatch):
    # QWEN_MM_AUDIO_RAW_B64 targets OpenAI-spec servers (e.g. vLLM), whose `input_audio.data`
    # is RAW base64 — the data-URL wrapper fails their decoding with "Incorrect padding"
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setenv("QWEN_MM_AUDIO_RAW_B64", "1")
    part = api_omni.omni_audio_part(str(wav))
    assert not part["input_audio"]["data"].startswith("data:")
    assert base64.b64decode(part["input_audio"]["data"]) == b"RIFF"
    assert part["input_audio"]["format"] == "wav"


# ── Audio budget: the ASR family compresses instead of shipping raw PCM ───────────────────────
def test_short_audio_travels_untouched(sample_media_av, tmp_path, monkeypatch):
    # a file that already fits keeps full fidelity — it matters for music tagging
    audio = str(tmp_path / "short.mp3")
    _common._encode_audio(sample_media_av, audio, kbps=32)
    cleanup: list[str] = []
    part = _common._local_audio_part(audio, cleanup, budget=_common.OMNI_MAX_UPLOAD_BYTES)
    assert cleanup == []  # nothing was transcoded
    assert part["input_audio"]["format"] == "mp3"


def test_video_audio_track_is_extracted_as_wav_when_it_fits(sample_media_av):
    cleanup: list[str] = []
    try:
        part = _common._local_audio_part(sample_media_av, cleanup, budget=_common.OMNI_MAX_UPLOAD_BYTES)
    finally:
        for path in cleanup:
            os.remove(path)
    assert part["input_audio"]["format"] == "wav"  # lossless 16 kHz mono is best for ASR
    assert len(cleanup) == 1


def test_audio_falls_back_to_fitted_mp3_when_wav_would_not_fit(sample_media_av):
    # 3s of 16 kHz mono PCM is ~96 kB; under a 30 kB budget the wav path is skipped for MP3
    cleanup: list[str] = []
    try:
        part = _common._local_audio_part(sample_media_av, cleanup, budget=30_000)
        size = os.path.getsize(cleanup[0])
    finally:
        for path in cleanup:
            os.remove(path)
    assert part["input_audio"]["format"] == "mp3"
    assert 0 < size <= 30_000


def test_fit_audio_rejects_a_track_too_long_for_the_floor_bitrate(sample_media_av, tmp_path):
    # 3s cannot fit 500 bytes even at 16 kbps: raise the actionable error, don't ship a doomed request
    out = str(tmp_path / "nope.mp3")
    with pytest.raises(_common._InlineBudgetExceeded, match="too long"):
        _common._fit_audio(sample_media_av, out, budget=500, duration=3.0)


# ── Over-cap local video: OSS upload when configured, else frames + audio ────────────────────
def _too_long(*a, **kw):
    raise _common._InlineBudgetExceeded("video is too long to upload inline")


def test_over_cap_video_is_uploaded_when_oss_is_configured(monkeypatch, sample_media_av):
    monkeypatch.setattr(_common, "_preprocess_video", _too_long)
    monkeypatch.setattr(_common.oss, "is_upload_configured", lambda: True)
    monkeypatch.setattr(_common.oss, "upload_and_sign", lambda path, **kw: "https://oss.example/signed.mp4")
    sent = {}
    monkeypatch.setattr(_common, "call_omni", lambda **kw: (sent.setdefault("m", kw["messages"]), "ok")[1])

    oav.get_handler("omni_av_caption")({"file_path": sample_media_av})
    parts = sent["m"][0]["content"]
    assert parts[0]["video_url"]["url"] == "https://oss.example/signed.mp4"
    assert len(parts) == 2  # just the video + the prompt: no frame split needed


def test_over_cap_video_is_split_into_frames_and_audio_without_oss(monkeypatch, sample_media_av):
    monkeypatch.setattr(_common, "_preprocess_video", _too_long)
    monkeypatch.setattr(_common.oss, "is_upload_configured", lambda: False)
    sent = {}
    monkeypatch.setattr(_common, "call_omni", lambda **kw: (sent.setdefault("m", kw["messages"]), "ok")[1])

    oav.get_handler("omni_av_caption")({"file_path": sample_media_av, "fps": 2})
    frames, audio, note, prompt = sent["m"][0]["content"]
    assert frames["type"] == "video" and len(frames["video"]) >= api_omni.OMNI_MIN_FRAMES
    assert all(url.startswith("data:image/jpeg;base64,") for url in frames["video"])
    assert audio["type"] == "input_audio" and audio["input_audio"]["format"] == "mp3"
    # the frame list has no time base of its own, so the note must supply the timestamps
    assert "#1=0s" in note["text"] and "timestamp" in note["text"]
    assert prompt["type"] == "text"
    assert api_omni.inline_b64_bytes(sent["m"]) <= api_omni.OMNI_MAX_B64_BYTES


def test_frames_fallback_is_used_when_the_oss_upload_fails(monkeypatch, sample_media_av):
    monkeypatch.setattr(_common, "_preprocess_video", _too_long)
    monkeypatch.setattr(_common.oss, "is_upload_configured", lambda: True)
    monkeypatch.setattr(_common, "_transcode_and_upload", lambda *a, **kw: (_ for _ in ()).throw(OSError("no bucket")))
    sent = {}
    monkeypatch.setattr(_common, "call_omni", lambda **kw: (sent.setdefault("m", kw["messages"]), "ok")[1])

    oav.get_handler("omni_av_caption")({"file_path": sample_media_av})
    assert sent["m"][0]["content"][0]["type"] == "video"  # degraded to frames, not an error


def test_frames_are_thinned_until_they_fit(monkeypatch, sample_media_av):
    # a budget too small for the estimated frame count: both parts come off the same total, and the
    # frame list is thinned rather than overshooting it
    monkeypatch.setattr(_common, "_preprocess_video", _too_long)
    monkeypatch.setattr(_common.oss, "is_upload_configured", lambda: False)
    monkeypatch.setattr(_common, "_INLINE_B64_BUDGET", 60_000)
    sent = {}
    monkeypatch.setattr(_common, "call_omni", lambda **kw: (sent.setdefault("m", kw["messages"]), "ok")[1])

    oav.get_handler("omni_av_caption")({"file_path": sample_media_av, "fps": 10})
    frames = sent["m"][0]["content"][0]["video"]
    assert len(frames) < 30  # 3s at 10 fps would be 30 frames; the budget forced fewer
    assert api_omni.inline_b64_bytes(sent["m"]) <= 60_000


def test_frames_are_capped_at_the_base64_image_limit(sample_media_av):
    # Qwen3.5-Omni takes at most 250 base64 images; a high fps must not push the list past that
    # (a few seeks at the very end of the fixture come back empty, so the count lands just under)
    urls, stamps = _common._fit_frames(sample_media_av, duration=3.0, fps=100.0, max_pixels=200704, budget=9_700_000)
    assert api_omni.OMNI_MAX_B64_FRAMES * 0.9 <= len(urls) == len(stamps) <= api_omni.OMNI_MAX_B64_FRAMES
    assert stamps == sorted(stamps)


# ── Live reachability (skipped without a key) ────────────────────────────────────────────────────
HAS_DASHSCOPE = bool(get_env("DASHSCOPE_API_KEY"))
HAS_FFMPEG = _common.find_tool  # presence of the resolver; the fixture skips if ffmpeg is absent


@pytest.mark.reachability
@pytest.mark.skipif(not HAS_DASHSCOPE, reason="DASHSCOPE_API_KEY not set")
def test_omni_asr_reachable(sample_media_av):
    blocks = oav.get_handler("omni_asr")({"file_path": sample_media_av})
    assert blocks and blocks[0]["type"] == "text"
    low = blocks[0]["text"].lower()
    assert not any(x in low for x in ("no api key", "no api-key", "invalid api", "connection error"))
