"""Offline tests for the API capability's general-purpose Omni perception tool."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import qwen_mm_plugins_api as api
from qwen_mm_plugins_api.omni import perceive_media
from shared import omni_media


def _json_block(blocks: list[dict]) -> dict:
    assert blocks[0]["type"] == "text"
    return json.loads(blocks[0]["text"])


def test_api_registers_general_perception_tool():
    tool = next(tool for tool in api.list_tools() if tool["name"] == "perceive_media")
    properties = tool["inputSchema"]["properties"]
    assert properties["media_type"]["enum"] == ["auto", "audio", "video"]
    assert properties["max_tokens"]["default"] == 65536
    assert {"start_time", "end_time"} <= properties.keys()
    assert "chunk_seconds" not in properties
    assert "workers" not in properties


def test_dry_run_does_not_read_media_or_call_model(monkeypatch):
    monkeypatch.setattr(perceive_media, "require_file", lambda *_a, **_kw: pytest.fail("must not read media"))
    monkeypatch.setattr(perceive_media, "call_omni", lambda **_kw: pytest.fail("must not call model"))

    result = _json_block(
        perceive_media.handle({"file_path": "/does/not/exist.mp4", "prompt": "Summarize it", "dry_run": True})
    )

    assert result["media_type"] == "auto"
    assert result["max_tokens"] == 65536
    assert result["prompt"] == "Summarize it"
    assert "No media was read" in result["note"]


def test_agent_prompt_is_sent_unchanged_in_one_call(monkeypatch, tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    prepared = {"type": "video_url", "video_url": {"url": "https://example.com/fitted.mp4"}}
    delivery = {}
    request = {}

    def fake_build(*args, **kwargs):
        delivery["args"] = args
        delivery["kwargs"] = kwargs
        return [prepared]

    def fake_call(**kwargs):
        request.update(kwargs)
        return "model answer", None

    monkeypatch.setattr(perceive_media, "require_dep", lambda *_a, **_kw: None)
    monkeypatch.setattr(perceive_media, "build_media_parts", fake_build)
    monkeypatch.setattr(perceive_media, "call_omni", fake_call)

    blocks = perceive_media.handle(
        {
            "file_path": str(source),
            "prompt": "Find every visible title.",
            "base_url": "https://example.com/v1",
            "api_key": "secret",
        }
    )

    assert blocks == [{"type": "text", "text": "model answer"}]
    assert delivery["args"][0] == str(source)
    assert delivery["args"][1] == "auto"
    assert delivery["kwargs"]["base_url"] == "https://example.com/v1"
    assert delivery["kwargs"]["api_key"] == "secret"
    assert request["max_tokens"] == 65536
    assert request["messages"] == [
        {"role": "user", "content": [prepared, {"type": "text", "text": "Find every visible title."}]}
    ]


def test_extensionless_remote_url_can_be_forced_to_video(monkeypatch):
    captured = {}

    def fake_build(*args, **_kwargs):
        captured["mode"] = args[1]
        return [{"type": "video_url", "video_url": {"url": args[0]}}]

    monkeypatch.setattr(perceive_media, "require_dep", lambda *_a, **_kw: None)
    monkeypatch.setattr(perceive_media, "build_media_parts", fake_build)
    monkeypatch.setattr(perceive_media, "call_omni", lambda **_kwargs: ("ok", None))

    blocks = perceive_media.handle(
        {
            "file_path": "https://example.com/download?id=1",
            "prompt": "Describe it",
            "media_type": "video",
        }
    )

    assert blocks == [{"type": "text", "text": "ok"}]
    assert captured["mode"] == "video"


def test_local_time_range_is_forwarded_as_start_and_duration(monkeypatch, tmp_path):
    source = tmp_path / "long-video.mp4"
    source.write_bytes(b"video")
    captured = {}

    def fake_build(*_args, **kwargs):
        captured.update(kwargs)
        return [{"type": "video_url", "video_url": {"url": "https://example.com/clip.mp4"}}]

    monkeypatch.setattr(perceive_media, "require_dep", lambda *_a, **_kw: None)
    monkeypatch.setattr(perceive_media, "normalize_local_range", lambda _path, start, duration: (start, duration))
    monkeypatch.setattr(perceive_media, "build_media_parts", fake_build)
    monkeypatch.setattr(perceive_media, "call_omni", lambda **_kwargs: ("ok", None))

    blocks = perceive_media.handle(
        {
            "file_path": str(source),
            "prompt": "Inspect this interval",
            "start_time": 120.0,
            "end_time": 145.5,
        }
    )

    assert blocks == [
        {"type": "text", "text": "ok"},
        {
            "type": "text",
            "text": (
                "Media interval: requested [120s, 145.5s], processed [120s, 145.5s]. "
                "Model-visible timestamps are relative to the processed interval; add 120s to map them "
                "to the source timeline."
            ),
        },
    ]
    assert captured["start_time"] == 120.0
    assert captured["duration"] == 25.5


def test_clamped_time_range_is_reported_to_the_agent(monkeypatch, tmp_path):
    source = tmp_path / "short-video.mp4"
    source.write_bytes(b"video")
    captured = {}

    monkeypatch.setattr(perceive_media, "require_dep", lambda *_a, **_kw: None)
    monkeypatch.setattr(perceive_media, "normalize_local_range", lambda _path, start, _duration: (start, 1.0))
    monkeypatch.setattr(
        perceive_media,
        "build_media_parts",
        lambda *_args, **kwargs: captured.update(kwargs) or [{"type": "video_url"}],
    )
    monkeypatch.setattr(perceive_media, "call_omni", lambda **_kwargs: ("ok", None))

    blocks = perceive_media.handle(
        {
            "file_path": str(source),
            "prompt": "Inspect this interval",
            "start_time": 2.0,
            "end_time": 7.0,
        }
    )

    assert captured["duration"] == 1.0
    assert blocks == [
        {"type": "text", "text": "ok"},
        {
            "type": "text",
            "text": (
                "Media interval: requested [2s, 7s], processed [2s, 3s]; end clamped to the media duration. "
                "Model-visible timestamps are relative to the processed interval; add 2s to map them "
                "to the source timeline."
            ),
        },
    ]


@pytest.mark.parametrize(
    "arguments,error",
    [
        ({"start_time": 10.0}, "provided together"),
        ({"start_time": 10.0, "end_time": 5.0}, "greater than"),
    ],
)
def test_invalid_time_range_is_rejected(arguments, error):
    blocks = perceive_media.handle(
        {"file_path": "/does/not/matter.mp4", "prompt": "Inspect it", "dry_run": True, **arguments}
    )
    assert error in blocks[0]["text"]


def test_remote_time_range_is_rejected():
    blocks = perceive_media.handle(
        {
            "file_path": "https://example.com/video.mp4",
            "prompt": "Inspect it",
            "start_time": 10.0,
            "end_time": 20.0,
            "dry_run": True,
        }
    )
    assert "requires a local media file" in blocks[0]["text"]


def test_oss_url_is_treated_as_remote_media(monkeypatch):
    monkeypatch.setattr(perceive_media, "require_file", lambda *_a, **_kw: pytest.fail("must not read oss URL"))
    monkeypatch.setattr(perceive_media, "require_dep", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        perceive_media,
        "build_media_parts",
        lambda source, *_a, **_kw: [{"type": "video_url", "video_url": {"url": source}}],
    )
    monkeypatch.setattr(perceive_media, "call_omni", lambda **_kwargs: ("ok", None))

    assert perceive_media.handle({"file_path": "oss://temporary/video.mp4", "prompt": "Describe it"}) == [
        {"type": "text", "text": "ok"}
    ]


def test_shared_delivery_attempts_temporary_oss_with_endpoint(monkeypatch, tmp_path):
    source = tmp_path / "large.mp4"
    source.write_bytes(b"video")
    expected = [{"type": "video_url", "video_url": {"url": "oss://temporary/video.mp4"}}]
    captured = {}

    def fake_temporary(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(omni_media, "temporary_oss_parts", fake_temporary)

    parts = omni_media.build_media_parts(
        str(source),
        "auto",
        1.0,
        200704,
        [],
        3600,
        "qwen3.5-omni-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="key",
    )

    assert parts == expected
    assert captured["args"][-2:] == ("https://dashscope.aliyuncs.com/compatible-mode/v1", "key")


@pytest.mark.parametrize(
    ("requested_duration", "expected_duration"),
    [(None, 20.0), (50.0, 20.0), (10.0, 10.0)],
)
def test_shared_delivery_normalizes_local_range_to_source_duration(
    monkeypatch,
    requested_duration,
    expected_duration,
):
    captured = {}

    monkeypatch.setattr(omni_media, "media_duration", lambda _path: 100.0)
    monkeypatch.setattr(omni_media, "has_video_stream", lambda _path: False)

    def fake_local_audio(_path, _cleanup, **kwargs):
        captured.update(kwargs)
        return {"type": "input_audio"}

    monkeypatch.setattr(omni_media, "local_audio_part", fake_local_audio)

    omni_media.build_media_parts(
        "audio.wav",
        "auto",
        1.0,
        200704,
        [],
        3600,
        "qwen3.5-omni-plus",
        start_time=80.0,
        duration=requested_duration,
    )

    assert captured["start_time"] == 80.0
    assert captured["duration"] == expected_duration


def test_shared_delivery_rejects_range_starting_after_media_end(monkeypatch):
    monkeypatch.setattr(omni_media, "media_duration", lambda _path: 100.0)

    with pytest.raises(ValueError, match=r"start_time \(100s\) >= media duration \(100s\)"):
        omni_media.build_media_parts(
            "video.mp4",
            "auto",
            1.0,
            200704,
            [],
            3600,
            "qwen3.5-omni-plus",
            start_time=100.0,
            duration=10.0,
        )


def test_frames_fallback_reserves_one_data_uri_slot_for_audio(monkeypatch):
    cleanup: list[str] = []
    captured = {}

    monkeypatch.setattr(omni_media, "has_audio_stream", lambda _path: True)

    def fake_fit_audio(_source, output, **_kwargs):
        Path(output).write_bytes(b"audio")

    def fake_fit_frames(_source, duration, fps, _max_pixels, _budget, **_kwargs):
        count = int(duration * fps)
        captured["count"] = count
        return ["data:image/jpeg;base64,eA=="] * count, list(range(count))

    try:
        parts = omni_media.frames_and_audio_parts(
            "video.mp4",
            1.0,
            200704,
            cleanup,
            duration=1000.0,
            fit_audio_fn=fake_fit_audio,
            fit_frames_fn=fake_fit_frames,
        )
    finally:
        for path in cleanup:
            if os.path.exists(path):
                os.remove(path)

    assert captured["count"] == 249
    assert len(parts[0]["video"]) + 1 == 250


def test_model_error_is_returned_as_a_tool_error(monkeypatch, tmp_path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(perceive_media, "require_dep", lambda *_a, **_kw: None)
    monkeypatch.setattr(perceive_media, "build_media_parts", lambda *_a, **_kw: [])
    monkeypatch.setattr(perceive_media, "call_omni", lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")))

    blocks = perceive_media.handle({"file_path": str(source), "prompt": "Transcribe"})

    assert blocks[0]["text"] == "Error: RuntimeError: boom"
