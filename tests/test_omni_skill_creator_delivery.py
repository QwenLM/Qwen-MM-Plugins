"""Exercise shared media delivery through the creator's real tool/request boundary."""

import base64
import functools
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from qwen_mm_plugins_omni_skill_creator.tools import read_native_av as av
from shared import dashscope_upload, env, omni_media


@pytest.fixture
def wire(monkeypatch):
    calls = []
    monkeypatch.setattr(env, "_config_cache", {})
    for name in ("OMNI_AV_SOURCE_PATH", "OMNI_AV_SOURCE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("QWEN_MM_API_OMNI_MODEL", "qwen3.8-omni-flash")
    monkeypatch.setattr(omni_media.oss, "is_upload_configured", lambda: False)
    monkeypatch.setattr(omni_media, "has_video_stream", lambda p: str(p).endswith(".mp4"))
    monkeypatch.setattr("shared.video.video_duration_exceeds", lambda *_a: False)
    monkeypatch.setattr(dashscope_upload, "is_available", lambda *_a: False)

    client_type = httpx.Client

    def client(**options):
        def respond(request):
            calls.append(
                {
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "body": json.loads(request.content),
                    "options": options,
                }
            )
            events = [
                {
                    "choices": [
                        {
                            "delta": {"content": "### [00:00:00.000-00:00:01.000] observed event"},
                            "finish_reason": "stop",
                        }
                    ]
                },
                {"choices": [], "usage": {"prompt_tokens": 123}},
            ]
            data = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})

        return client_type(transport=httpx.MockTransport(respond), **options)

    monkeypatch.setattr(av.httpx, "Client", client)
    return calls


def large_file(path: Path) -> Path:
    with path.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024)
    return path


def content(wire):
    return wire[-1]["body"]["messages"][0]["content"]


@pytest.mark.parametrize("suffix", [".mp4", ".wav"])
def test_small_media_stays_original_and_never_uploads(wire, monkeypatch, tmp_path, suffix):
    path = tmp_path / ("small" + suffix)
    path.write_bytes(b"original bytes")
    upload = Mock(side_effect=AssertionError("small original media must not upload"))
    monkeypatch.setattr(dashscope_upload, "upload_temporary_file", upload)
    result = av.handle({"video_path": str(path), "prompt": "original prompt", "fps": 2})
    part = content(wire)[0]
    value = part["video_url"]["url"] if suffix == ".mp4" else part["input_audio"]["data"]
    assert base64.b64decode(value.split(",", 1)[1]) == path.read_bytes()
    assert "max_pixels" not in part
    assert content(wire)[-1] == {"type": "text", "text": "original prompt"}
    assert "MEDIA DELIVERY NOTICE" not in result[0]["text"]
    upload.assert_not_called()


@pytest.mark.parametrize("suffix", [".mp4", ".wav"])
def test_temporary_oss_success_reaches_model_from_tool(wire, monkeypatch, tmp_path, suffix):
    path = large_file(tmp_path / ("source" + suffix))
    monkeypatch.setattr(dashscope_upload, "is_available", lambda *_a: True)
    upload = Mock(return_value="oss://temporary/source" + suffix)
    monkeypatch.setattr(dashscope_upload, "upload_temporary_file", upload)
    result = av.handle({"video_path": str(path), "prompt": "original prompt", "fps": 2})
    assert not result[0]["text"].startswith("Error:")
    assert len(wire) == 1
    assert wire[0]["headers"]["x-dashscope-ossresourceresolve"] == "enable"
    part = content(wire)[0]
    value = part.get("video_url", {}).get("url") or part["input_audio"]["data"]
    assert value == "oss://temporary/source" + suffix
    assert "max_pixels" not in part
    assert upload.call_args.args[0] == str(path)
    assert upload.call_args.kwargs["model"] == "qwen3.8-omni-flash"
    assert "MEDIA DELIVERY NOTICE" not in result[0]["text"]


@pytest.mark.parametrize("failure", ["unavailable", "upload_failure"])
def test_temporary_failure_uses_original_configured_oss(wire, monkeypatch, tmp_path, failure):
    path = large_file(tmp_path / "source.mp4")
    monkeypatch.setattr(dashscope_upload, "is_available", lambda *_a: failure != "unavailable")
    monkeypatch.setattr(
        dashscope_upload, "upload_temporary_file", Mock(side_effect=RuntimeError("offline failed upload"))
    )
    monkeypatch.setattr(omni_media.oss, "is_upload_configured", lambda: True)
    own = Mock(return_value="https://media.example/original.mp4")
    monkeypatch.setattr(omni_media.oss, "upload_and_sign", own)
    result = av.handle({"video_path": str(path), "prompt": "original prompt"})
    assert own.call_args.args[0] == str(path)
    assert content(wire)[0]["video_url"]["url"] == "https://media.example/original.mp4"
    assert "x-dashscope-ossresourceresolve" not in wire[0]["headers"]
    assert "MEDIA DELIVERY NOTICE" not in result[0]["text"]


def test_shared_transcode_notice_and_cleanup(wire, monkeypatch, tmp_path):
    path = large_file(tmp_path / "source.mp4")
    generated = []

    def preprocess(_source, destination, _max_pixels, **_kwargs):
        Path(destination).write_bytes(b"fitted video bytes")
        generated.append(Path(destination))

    monkeypatch.setattr(
        omni_media,
        "local_video_parts",
        functools.partial(
            omni_media.local_video_parts,
            preprocess_video_fn=preprocess,
        ),
    )
    result = av.handle({"video_path": str(path), "prompt": "original prompt", "fps": 2})[0]["text"]
    assert content(wire)[0]["max_pixels"] == av.DEFAULT_OMNI_MAX_PIXELS
    assert content(wire)[0]["fps"] == 2
    assert "MEDIA DELIVERY NOTICE — NOT EVENT LOG CONTENT" in result
    assert "H.264/AAC" in result and "32 kbps" in result
    assert "Do not copy this notice" in result
    assert result.endswith("### [00:00:00.000-00:00:01.000] observed event")
    assert generated and all(not p.exists() for p in generated)
    assert path.exists()


@pytest.mark.parametrize("audio", [True, False])
def test_twenty_minute_frames_fallback_reports_real_spacing(wire, monkeypatch, tmp_path, audio):
    path = large_file(tmp_path / "source.mp4")
    duration = 1200.0
    seen = {}
    created = []
    monkeypatch.setattr(omni_media, "media_duration", lambda _p: duration)
    monkeypatch.setattr(omni_media, "has_audio_stream", lambda _p: audio)

    def fit_audio(_source, destination, **kwargs):
        Path(destination).write_bytes(b"complete fitted soundtrack")
        created.append(Path(destination))
        assert kwargs["duration"] == duration

    def fit_frames(_source, span, fps, _pixels, _budget, **_kwargs):
        seen["fps"] = fps
        count = 249 if audio else 250
        return (["data:image/jpeg;base64,eA=="] * count, [i * span / count for i in range(count)])

    frames = functools.partial(omni_media.frames_and_audio_parts, fit_audio_fn=fit_audio, fit_frames_fn=fit_frames)
    monkeypatch.setattr(
        omni_media,
        "local_video_parts",
        functools.partial(
            omni_media.local_video_parts,
            preprocess_video_fn=Mock(
                side_effect=omni_media.InlineBudgetExceeded("20 minute input exceeds inline budget")
            ),
            frames_and_audio_parts_fn=frames,
        ),
    )
    result = av.handle({"video_path": str(path), "prompt_template": "event_log"})[0]["text"]
    count = 249 if audio else 250
    assert len([p for p in content(wire) if p["type"] == "image_url"]) == count
    assert bool([p for p in content(wire) if p["type"] == "input_audio"]) == audio
    assert f"{count} sampled frames across 1200.00s" in result
    assert "effective 0.208 fps" in result
    assert "Short visual actions and small text may be missed" in result
    assert content(wire)[-1]["text"] == av.EVENT_LOG_PROMPT
    assert result.endswith("### [00:00:00.000-00:00:01.000] observed event")
    assert all(not p.exists() for p in created)
    if audio:
        assert seen["fps"] == pytest.approx(249 / 1200)


def test_range_uses_same_clip_and_offset_with_shared_upload(wire, monkeypatch, tmp_path):
    path = tmp_path / "original.mp4"
    path.write_bytes(b"original")
    clip = large_file(tmp_path / "clip.mp4")
    prepare = Mock(return_value=(str(clip), 47.98))
    monkeypatch.setattr(av, "prepare_clip", prepare)
    monkeypatch.setattr(dashscope_upload, "is_available", lambda *_a: True)
    upload = Mock(return_value="oss://temporary/clip.mp4")
    monkeypatch.setattr(dashscope_upload, "upload_temporary_file", upload)
    result = av.handle({"video_path": str(path), "start_sec": 50, "end_sec": 68, "fps": 2, "prompt_template": "zoom"})[
        0
    ]["text"]
    prepare.assert_called_once_with(str(path), 50, 68)
    assert upload.call_args.args[0] == str(clip)
    assert "ADD 47.98s" in result
    assert content(wire)[-1]["text"] == av.ZOOM_PROMPT
    assert path.exists() and not clip.exists()


def test_twenty_minute_original_upload_does_not_enter_sparse_fallback(wire, monkeypatch, tmp_path):
    path = large_file(tmp_path / "twenty-minute.mp4")
    monkeypatch.setattr(omni_media, "media_duration", lambda _p: 1200.0)
    monkeypatch.setattr(dashscope_upload, "is_available", lambda *_a: True)
    monkeypatch.setattr(dashscope_upload, "upload_temporary_file", Mock(return_value="oss://temporary/full.mp4"))
    monkeypatch.setattr(omni_media, "build_media_parts", Mock(side_effect=AssertionError("must keep original upload")))

    result = av.handle({"video_path": str(path), "prompt_template": "event_log"})[0]["text"]

    assert content(wire)[0]["video_url"]["url"] == "oss://temporary/full.mp4"
    assert not any(p["type"] == "image_url" for p in content(wire))
    assert "MEDIA DELIVERY NOTICE" not in result


def test_fallback_temp_files_are_cleaned_after_model_failure(wire, monkeypatch, tmp_path):
    source = large_file(tmp_path / "source.mp4")
    prepared = tmp_path / "fallback.mp3"
    monkeypatch.setattr(av, "_compress_to_inline", lambda *_a, **_kw: None)

    def build(_source, _mode, _fps, _pixels, cleanup, *_args, **_kwargs):
        prepared.write_bytes(b"fitted audio")
        cleanup.append(str(prepared))
        return [{"type": "input_audio", "input_audio": {"data": "data:;base64,eA==", "format": "mp3"}}]

    monkeypatch.setattr(omni_media, "build_media_parts", build)
    monkeypatch.setattr(av, "_openai_call", Mock(side_effect=RuntimeError("FATAL 400: test model rejection")))

    result = av.handle({"video_path": str(source), "prompt": "original prompt"})[0]["text"]

    assert result.startswith("Error: read_native_av failed:")
    assert not prepared.exists()
    assert source.exists()
