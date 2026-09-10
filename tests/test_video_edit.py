"""Offline tests for video-edit tool discovery and provider request/result handling."""

import sys
import types

import pytest

import qwen_mm_plugins_video_edit as ve

GENERATION_TOOLS = {
    "qwen_image",
    "qwen_tts",
    "minimax_tts",
    "wan_s2v",
    "wan_t2v",
    "happyhorse",
}


def test_lists_the_generation_tools():
    names = {t["name"] for t in ve.list_tools()}
    assert names == GENERATION_TOOLS


def test_every_tool_has_schema_and_handler():
    for tool in ve.list_tools():
        assert tool["inputSchema"]["type"] == "object"
        assert callable(ve.get_handler(tool["name"]))


def test_unknown_tool_has_no_handler():
    assert ve.get_handler("does_not_exist") is None


def test_wan_s2v_detect_surfaces_root_api_error(monkeypatch):
    import requests

    from qwen_mm_plugins_video_edit.tools import wan_s2v

    class Response:
        status_code = 400

        @staticmethod
        def json():
            return {"code": "InvalidURL", "message": "image is unreachable", "request_id": "req-1"}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(wan_s2v, "retry_call", lambda fn, *args, **kwargs: fn(*args, **kwargs))

    result = wan_s2v._detect("https://example.com/missing.png", "key")
    assert "InvalidURL" in result[0]["text"]
    assert "image is unreachable" in result[0]["text"]
    assert "req-1" in result[0]["text"]


def test_wan_27_translates_existing_size_interface(monkeypatch):
    from qwen_mm_plugins_video_edit.tools import wan_t2v
    from shared import api_dashscope

    calls = []

    class VideoSynthesis:
        @staticmethod
        def call(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(
                status_code=200,
                output=types.SimpleNamespace(task_status="SUCCEEDED", video_url="https://example.com/video.mp4"),
            )

    dashscope = types.ModuleType("dashscope")
    dashscope.VideoSynthesis = VideoSynthesis
    dashscope.base_http_api_url = None
    monkeypatch.setitem(sys.modules, "dashscope", dashscope)
    monkeypatch.setattr(wan_t2v, "get_env", lambda name: "key")
    monkeypatch.setattr(api_dashscope, "retry_call", lambda fn, **kwargs: fn(**kwargs))

    result = wan_t2v.handle(
        {
            "mode": "text_to_video",
            "prompt": "a cat runs",
            "size": "720*1280",
        }
    )

    assert not result[0]["text"].startswith("Error:")
    assert calls[0]["resolution"] == "720P"
    assert calls[0]["ratio"] == "9:16"
    assert "size" not in calls[0]


def test_wan_t2v_schema_keeps_size_parameter():
    from qwen_mm_plugins_video_edit.tools import wan_t2v

    schema = wan_t2v.WanT2vArgs.model_json_schema()
    assert "size" in schema["properties"]
    assert "resolution" not in schema["properties"]
    assert "ratio" not in schema["properties"]


@pytest.mark.parametrize("output_format", ["url", "hex"])
def test_minimax_saves_audio_and_returns_subtitles(monkeypatch, tmp_path, output_format):
    import requests

    from qwen_mm_plugins_video_edit.tools import minimax_tts

    audio = b"test-audio"
    response = {
        "base_resp": {"status_code": 0},
        "data": {
            "status": 2,
            "audio": audio.hex() if output_format == "hex" else "https://example.com/audio.mp3",
            "subtitle_file": "https://example.com/subtitles.json",
        },
    }
    monkeypatch.setattr(minimax_tts, "get_env", lambda name: "test-key")
    monkeypatch.setattr(minimax_tts, "_request", lambda *args: response)
    monkeypatch.setattr(
        requests, "get", lambda *args, **kwargs: types.SimpleNamespace(content=audio, raise_for_status=lambda: None)
    )
    output = minimax_tts.handle(
        {
            "text": "Hello",
            "voice": "narrator",
            "output_dir": str(tmp_path / "audio"),
            "output_format": output_format,
            "subtitle_enable": True,
        }
    )[0]["text"]
    assert "**Saved to**:" in output and "**Subtitle URL**: https://example.com/subtitles.json" in output
    files = list((tmp_path / "audio").glob("*.mp3"))
    assert len(files) == 1 and files[0].read_bytes() == audio
