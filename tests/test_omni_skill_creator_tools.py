"""Behavior tests for the omni-skill-creator MCP tools.

dedup_audio: the MFCC fingerprint used to be computed with librosa, which is not declared in
the capability's pyproject extra — the tool raised ModuleNotFoundError at call time while
passing every install/check-system gate (that's why these tests exist: zero coverage let an
undeclared heavy dependency ship). The fingerprint is now pure numpy; these tests pin the
discrimination behavior that dedup relies on.

read_native_av: env config used to be read with os.environ at import time, which silently
skipped the ~/.qwen-mm-plugins/config fallback layer that desktop-launched harnesses depend
on, and the model default had no repo-wide knob. These tests pin the call-time resolution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from conftest import HAS_FFMPEG

from qwen_mm_plugins_omni_skill_creator.tools import dedup_audio, read_native_av
from shared import env, omni_media

_SR = 8000


def _tone(freq: float, seconds: float = 2.0, phase: float = 0.0) -> np.ndarray:
    t = np.arange(int(_SR * seconds), dtype=np.float64) / _SR
    return (0.5 * np.sin(2 * np.pi * freq * t + phase)).astype(np.float32)


def test_fingerprint_is_deterministic():
    a = dedup_audio._mfcc_fingerprint(_tone(440))
    b = dedup_audio._mfcc_fingerprint(_tone(440))
    assert a is not None and b is not None
    assert np.array_equal(a, b)


def test_similar_clips_score_above_threshold():
    a = dedup_audio._mfcc_fingerprint(_tone(440))
    b = dedup_audio._mfcc_fingerprint(_tone(440, phase=0.05))
    assert dedup_audio._cosine_similarity(a, b) >= 0.95


def test_distinct_clips_score_below_threshold():
    a = dedup_audio._mfcc_fingerprint(_tone(440))
    for other in (_tone(3000), np.random.default_rng(0).uniform(-0.5, 0.5, _SR * 2).astype(np.float32)):
        assert dedup_audio._cosine_similarity(a, dedup_audio._mfcc_fingerprint(other)) < 0.95


def test_too_short_clip_has_no_fingerprint():
    assert dedup_audio._mfcc_fingerprint(_tone(440)[:400]) is None


@pytest.fixture(scope="module")
def two_tone_video(tmp_path_factory):
    """8s video: 440 Hz sine for the first half, 3000 Hz for the second (requires ffmpeg)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    path = tmp_path_factory.mktemp("media") / "two_tone.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=8:size=160x120:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=3000:duration=4",
            "-filter_complex",
            "[1:a][2:a]concat=n=2:v=0:a=1[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return str(path)


def test_handle_dedups_overlapping_and_keeps_distinct(two_tone_video):
    content = dedup_audio.handle(
        {
            "video_path": two_tone_video,
            "segments": [[0.5, 3.0], [1.0, 3.5], [5.0, 7.5]],
        }
    )
    result = json.loads(content[0]["text"])
    # [0.5,3.0] and [1.0,3.5] sit on the same 440 Hz half -> the second is its duplicate;
    # [5.0,7.5] is the 3000 Hz half -> kept as distinct.
    assert [u["segment"] for u in result["unique"]] == [[0.5, 3.0], [5.0, 7.5]]
    assert [d["duplicate_of"] for d in result["duplicates"]] == [[0.5, 3.0]]


@pytest.fixture
def no_model_env(monkeypatch):
    """Pin the model var off AND blank the cached config layer, so resolution tests only see
    what the test itself sets (a dev machine's ~/.qwen-mm-plugins/config could set these)."""
    monkeypatch.delenv("QWEN_MM_API_OMNI_MODEL", raising=False)
    import shared.env

    monkeypatch.setattr(shared.env, "_config_cache", {})
    return monkeypatch


def test_default_model_reads_repo_wide_var(no_model_env):
    no_model_env.setenv("QWEN_MM_API_OMNI_MODEL", "repo-omni-model")
    assert read_native_av._default_model() == "repo-omni-model"


def test_default_model_hard_default(no_model_env):
    assert read_native_av._default_model() == "qwen3.8-omni-flash"


def test_config_file_layer_feeds_default_model(no_model_env, tmp_path):
    import shared.env

    cfg = tmp_path / "config"
    cfg.write_text("QWEN_MM_API_OMNI_MODEL=config-file-model\n", encoding="utf-8")
    no_model_env.setattr(shared.env, "config_file", lambda: str(cfg))
    no_model_env.setattr(shared.env, "_config_cache", None)
    assert read_native_av._default_model() == "config-file-model"


def test_native_av_connection_uses_only_shared_dashscope_fields(no_model_env):
    no_model_env.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    no_model_env.setenv("DASHSCOPE_API_KEY", "shared-key")
    no_model_env.setenv("OMNI_AV_OPENAI_BASE_URL", "https://legacy.example/v1")
    no_model_env.setenv("OMNI_API_KEY", "legacy-omni-key")
    no_model_env.setenv("API_KEY", "unrelated-generic-key")

    assert read_native_av._default_openai_base() == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert read_native_av._resolve_key() == "shared-key"


def test_native_av_connection_reads_shared_config_file(no_model_env, tmp_path):
    import shared.env

    for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY"):
        no_model_env.delenv(name, raising=False)
    cfg = tmp_path / "config"
    cfg.write_text(
        "DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1\nDASHSCOPE_API_KEY=config-key\n",
        encoding="utf-8",
    )
    no_model_env.setattr(shared.env, "config_file", lambda: str(cfg))
    no_model_env.setattr(shared.env, "_config_cache", None)

    assert read_native_av._default_openai_base() == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert read_native_av._resolve_key() == "config-key"


def test_native_av_does_not_fall_back_to_generic_or_legacy_keys(no_model_env):
    for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY"):
        no_model_env.delenv(name, raising=False)
    no_model_env.setenv("OMNI_AV_OPENAI_BASE_URL", "https://legacy.example/v1")
    no_model_env.setenv("OMNI_API_KEY", "legacy-omni-key")
    no_model_env.setenv("API_KEY", "unrelated-generic-key")

    assert read_native_av._default_openai_base() == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        read_native_av._resolve_key()


@pytest.fixture
def oss_delivery(monkeypatch, tmp_path):
    for name in os.environ:
        if name.startswith("OSS_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(env, "_config_cache", {})
    monkeypatch.setenv("HOME", str(tmp_path))
    for name, value in {
        "OSS_AK": "test-ak",
        "OSS_SK": "test-sk",
        "OSS_ENDPOINT": "http://oss-cn-test-internal.example.test",
        "OSS_BUCKET": "test-bucket",
    }.items():
        monkeypatch.setenv(name, value)

    objects = {}
    bucket = Mock()
    bucket.put_object.side_effect = lambda key, stream: objects.__setitem__(key, stream.read())
    bucket.sign_url.side_effect = lambda method, key, ttl, **kwargs: f"http://media.example.test/{key}"
    sdk = SimpleNamespace(Auth=Mock(), Bucket=Mock(return_value=bucket))
    monkeypatch.setitem(sys.modules, "oss2", sdk)
    # Encoded-size budget: b"clip" -> 8 bytes base64 (fits), larger fixtures do not.
    monkeypatch.setattr(omni_media, "OMNI_MAX_B64_BYTES", 8)
    compress = Mock()
    monkeypatch.setattr(read_native_av, "_compress_to_inline", compress)
    return SimpleNamespace(sdk=sdk, bucket=bucket, objects=objects, compress=compress)


def test_oss_delivery_uses_shared_defaults(oss_delivery, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video content")
    key = f"tmp/video_clips/{hashlib.md5(path.read_bytes()).hexdigest()}.mp4"

    assert read_native_av._deliver_local(str(path)) == ("url", f"https://media.example.test/{key}")
    assert oss_delivery.objects == {key: b"video content"}
    oss_delivery.sdk.Auth.assert_called_once_with("test-ak", "test-sk")
    oss_delivery.sdk.Bucket.assert_called_once_with(
        oss_delivery.sdk.Auth.return_value, "http://oss-cn-test.example.test", "test-bucket"
    )
    oss_delivery.bucket.sign_url.assert_called_once_with("GET", key, 7200, slash_safe=True)
    oss_delivery.compress.assert_not_called()


def test_oss_delivery_uses_config_file_and_env_override(oss_delivery, monkeypatch, tmp_path):
    for name in ("OSS_AK", "OSS_SK", "OSS_ENDPOINT", "OSS_BUCKET"):
        monkeypatch.delenv(name)
    cfg = tmp_path / "config"
    cfg.write_text(
        "OSS_AK=config-ak\nOSS_SK=config-sk\nOSS_ENDPOINT=http://oss-cn-test-internal.example.test\n"
        "OSS_BUCKET=config-bucket\nOSS_VIDEO_CLIP_PREFIX=/custom/clips/\nOSS_URL_EXPIRY=1234\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env, "config_file", lambda: str(cfg))
    monkeypatch.setattr(env, "_config_cache", None)
    monkeypatch.setenv("OSS_BUCKET", "env-bucket")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video content")

    mode, url = read_native_av._deliver_local(str(path))
    key = next(iter(oss_delivery.objects))
    assert mode == "url" and url == f"https://media.example.test/{key}"
    assert key.startswith("custom/clips/")
    oss_delivery.sdk.Auth.assert_called_once_with("config-ak", "config-sk")
    assert oss_delivery.sdk.Bucket.call_args.args[2] == "env-bucket"
    oss_delivery.bucket.sign_url.assert_called_once_with("GET", key, 1234, slash_safe=True)


def test_oss_same_basename_different_content_gets_different_objects(oss_delivery, tmp_path):
    first = tmp_path / "first" / "video.mp4"
    second = tmp_path / "second" / "video.mp4"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first video")
    second.write_bytes(b"second video")

    first_url = read_native_av._deliver_local(str(first))[1]
    second_url = read_native_av._deliver_local(str(second))[1]
    assert first_url != second_url
    assert len(oss_delivery.objects) == 2
    assert set(oss_delivery.objects.values()) == {b"first video", b"second video"}
    assert read_native_av._deliver_local(str(first))[1] == first_url
    assert len(oss_delivery.objects) == 2

    first.write_bytes(b"updated video")
    assert read_native_av._deliver_local(str(first))[1] not in (first_url, second_url)
    assert len(oss_delivery.objects) == 3


def test_inline_delivery_does_not_touch_oss(oss_delivery, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"clip")
    assert read_native_av._deliver_local(str(path)) == ("inline", "Y2xpcA==", "video/mp4")
    oss_delivery.sdk.Bucket.assert_not_called()
    oss_delivery.compress.assert_not_called()


def test_inline_delivery_uses_shared_base64_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(omni_media, "OMNI_MAX_B64_BYTES", 8)
    fits = tmp_path / "fits.mp4"
    too_large = tmp_path / "too-large.mp4"
    fits.write_bytes(b"123456")  # base64 length 8
    too_large.write_bytes(b"1234567")  # base64 length 12

    assert read_native_av._fits_inline_budget(str(fits)) is True
    assert read_native_av._fits_inline_budget(str(too_large)) is False
    with pytest.raises(ValueError, match="after base64"):
        read_native_av._read_local_inline(str(too_large))


def test_temporary_oss_upload_precedes_existing_oss_and_preserves_original(monkeypatch, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"original video bytes")
    monkeypatch.setattr(omni_media, "OMNI_MAX_B64_BYTES", 4)
    temporary = Mock(return_value="oss://temporary/model/video.mp4")
    monkeypatch.setattr(read_native_av, "_temporary_oss_upload", temporary)
    monkeypatch.setattr(
        read_native_av,
        "_oss_upload_and_sign",
        lambda *_a, **_k: pytest.fail("existing OSS must not run after temporary upload succeeds"),
    )
    monkeypatch.setattr(
        read_native_av,
        "_compress_to_inline",
        lambda *_a, **_k: pytest.fail("compression must not run after original upload succeeds"),
    )

    assert read_native_av._deliver_local(
        str(path), model="dashscope.qwen3.8-omni-flash", base_url="https://gateway/v1", api_key="key"
    ) == ("url", "oss://temporary/model/video.mp4")
    temporary.assert_called_once_with(
        str(path),
        model="dashscope.qwen3.8-omni-flash",
        base_url="https://gateway/v1",
        api_key="key",
    )


def test_temporary_upload_uses_shared_dashscope_adapter(monkeypatch, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"original")
    monkeypatch.setattr(read_native_av.dashscope_upload, "is_available", lambda base, key: True)
    upload = Mock(return_value="oss://temporary/model/video.mp4")
    monkeypatch.setattr(read_native_av.dashscope_upload, "upload_temporary_file", upload)

    assert (
        read_native_av._temporary_oss_upload(
            str(path), model="model", base_url="https://dashscope.example/v1", api_key="secret"
        )
        == "oss://temporary/model/video.mp4"
    )
    upload.assert_called_once_with(str(path), base_url="https://dashscope.example/v1", api_key="secret", model="model")


def test_temporary_upload_failure_returns_to_existing_delivery(monkeypatch, tmp_path, caplog):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"original")
    monkeypatch.setattr(read_native_av.dashscope_upload, "is_available", lambda base, key: True)
    monkeypatch.setattr(
        read_native_av.dashscope_upload,
        "upload_temporary_file",
        Mock(side_effect=RuntimeError("policy unavailable")),
    )

    with caplog.at_level(logging.INFO):
        assert (
            read_native_av._temporary_oss_upload(
                str(path), model="model", base_url="https://dashscope.example/v1", api_key="secret"
            )
            is None
        )
    assert "temporary OSS upload failed" in caplog.text


def test_temporary_oss_request_adds_resource_resolve_header(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            payload = {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10},
            }
            return iter(["data: " + json.dumps(payload), "", "data: [DONE]", ""])

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, method, url, *, headers, json):
            assert method == "POST"
            captured.update(url=url, headers=headers, body=json)
            return Response()

    monkeypatch.setattr(read_native_av.httpx, "Client", Client)
    monkeypatch.setattr(read_native_av, "_openai_key", lambda: "key")

    result = read_native_av._openai_call(
        "qwen3.8-omni-flash",
        "oss://temporary/model/video.mp4",
        "same prompt",
        base="https://dashscope.example/v1",
    )

    assert result.text == "ok"
    assert captured["headers"]["X-DashScope-OssResourceResolve"] == "enable"
    assert captured["body"]["messages"][0]["content"][0]["video_url"]["url"].startswith("oss://")

    read_native_av._openai_call(
        "qwen3.8-omni-flash",
        "https://media.example/video.mp4",
        "same prompt",
        base="https://dashscope.example/v1",
    )
    assert "X-DashScope-OssResourceResolve" not in captured["headers"]


def test_compression_notice_is_in_summary_not_model_event_log(monkeypatch, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    seen = {}

    def deliver(file_path, **kwargs):
        assert file_path == str(path)
        kwargs["delivery_notes"].append("quality was reduced for transport")
        return "inline", "dmlkZW8=", "video/mp4"

    def perceive(data_b64, mime, prompt, **kwargs):
        seen.update(data_b64=data_b64, mime=mime, prompt=prompt, kwargs=kwargs)
        return read_native_av.AVResult(text="### [00:00:00.000-00:00:01.000] event — unchanged", reasoning="")

    monkeypatch.setattr(read_native_av, "_deliver_local", deliver)
    monkeypatch.setattr(read_native_av, "perceive_inline", perceive)
    monkeypatch.setattr(read_native_av, "_default_openai_base", lambda: "https://gateway/v1")
    monkeypatch.setattr(read_native_av, "_openai_key", lambda: "key")

    text = read_native_av.handle({"video_path": str(path), "prompt": "same prompt"})[0]["text"]

    assert seen["prompt"] == "same prompt"
    assert "MEDIA DELIVERY NOTICE — NOT EVENT LOG CONTENT" in text
    assert "quality was reduced for transport" in text
    assert "Do not copy this notice into `.build/video_events.md`" in text
    assert text.endswith("### [00:00:00.000-00:00:01.000] event — unchanged")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not available")
def test_real_video_compression_fallback_fits_shared_budget_and_cleans_temp(monkeypatch, tmp_path):
    source = tmp_path / "lossless.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=3:size=640x360:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    # Keep all generated fallback files inside this test-owned directory.
    monkeypatch.setattr(omni_media.tempfile, "tempdir", str(tmp_path))
    probe = read_native_av._compress_to_inline(str(source))
    assert probe is not None
    compressed_b64 = omni_media.b64_len(os.path.getsize(probe))
    original_b64 = omni_media.b64_len(os.path.getsize(source))
    assert compressed_b64 < original_b64
    omni_media.cleanup_files([probe])
    monkeypatch.setattr(omni_media, "OMNI_MAX_B64_BYTES", (compressed_b64 + original_b64) // 2)
    monkeypatch.setattr(read_native_av, "_temporary_oss_upload", lambda *_a, **_k: None)
    monkeypatch.setattr(read_native_av, "_oss_upload_and_sign", lambda *_a, **_k: None)
    notes = []

    mode, data_b64, mime = read_native_av._deliver_local(str(source), delivery_notes=notes)

    assert mode == "inline" and mime == "video/mp4"
    assert len(data_b64) <= omni_media.OMNI_MAX_B64_BYTES
    assert notes and "H.264/AAC fallback" in notes[0]
    assert not list(tmp_path.glob("creator_av_small_*"))


def test_harness_url_mapping_still_bypasses_local_delivery(monkeypatch, tmp_path):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"video")
    monkeypatch.setenv("OMNI_AV_SOURCE_PATH", str(path))
    monkeypatch.setenv("OMNI_AV_SOURCE_URL", "https://harness.example/source.mp4")
    monkeypatch.setattr(
        read_native_av,
        "_deliver_local",
        lambda *_a, **_k: pytest.fail("harness URL must bypass local delivery"),
    )
    seen = {}

    def perceive(url, prompt, **kwargs):
        seen.update(url=url, prompt=prompt, kwargs=kwargs)
        return read_native_av.AVResult(text="mapped", reasoning="", usage={"prompt_tokens": 12})

    monkeypatch.setattr(read_native_av, "perceive_url", perceive)
    text = read_native_av.handle({"video_path": str(path), "prompt": "same prompt"})[0]["text"]

    assert seen["url"] == "https://harness.example/source.mp4"
    assert seen["prompt"] == "same prompt"
    assert "via harness-url" in text and "prompt_tokens: 12" in text


def test_http_url_input_still_bypasses_local_delivery(monkeypatch):
    monkeypatch.setattr(
        read_native_av,
        "_deliver_local",
        lambda *_a, **_k: pytest.fail("remote URL must bypass local delivery"),
    )
    seen = {}

    def perceive(url, prompt, **kwargs):
        seen.update(url=url, prompt=prompt, kwargs=kwargs)
        return read_native_av.AVResult(text="remote", reasoning="")

    monkeypatch.setattr(read_native_av, "perceive_url", perceive)
    text = read_native_av.handle({"video_path": "https://media.example/video.mp4", "prompt": "same prompt"})[0]["text"]

    assert seen["url"] == "https://media.example/video.mp4"
    assert seen["prompt"] == "same prompt"
    assert "remote-url" in text


@pytest.mark.parametrize("failure", ["unconfigured", "missing_sdk", "upload", "sign"])
def test_oss_failure_preserves_compression_fallback(oss_delivery, monkeypatch, tmp_path, caplog, failure):
    path = tmp_path / "video.mp4"
    small = tmp_path / "small.mp4"
    path.write_bytes(b"large video")
    small.write_bytes(b"clip")
    oss_delivery.compress.return_value = str(small)
    if failure == "unconfigured":
        monkeypatch.delenv("OSS_BUCKET")
    elif failure == "missing_sdk":
        monkeypatch.setitem(sys.modules, "oss2", None)
    elif failure == "upload":
        oss_delivery.bucket.put_object.side_effect = RuntimeError("upload failed")
    else:
        oss_delivery.bucket.sign_url.side_effect = RuntimeError("sign failed")

    with caplog.at_level(logging.INFO):
        assert read_native_av._deliver_local(str(path)) == ("inline", "Y2xpcA==", "video/mp4")
    oss_delivery.compress.assert_called_once_with(str(path))
    if failure in ("unconfigured", "missing_sdk"):
        oss_delivery.sdk.Bucket.assert_not_called()
        assert "OSS_BUCKET" in caplog.text
    else:
        assert f"{failure} failed" in caplog.text


def test_no_oss_and_failed_compression_reports_delivery_error(oss_delivery, monkeypatch, tmp_path):
    monkeypatch.delenv("OSS_BUCKET")
    oss_delivery.compress.return_value = None
    path = tmp_path / "video.mp4"
    path.write_bytes(b"large video")
    with pytest.raises(RuntimeError, match="cannot deliver local file"):
        read_native_av._deliver_local(str(path))


@pytest.mark.parametrize("expiry, expected", [(None, 7200), ("1800", 1800), ("invalid", 7200)])
def test_oss_uri_uses_shared_signing_without_upload_bucket(oss_delivery, monkeypatch, expiry, expected):
    monkeypatch.delenv("OSS_BUCKET")
    if expiry is not None:
        monkeypatch.setenv("OSS_URL_EXPIRY", expiry)
    assert read_native_av._sign_oss_uri("oss://source-bucket/folder/video.mp4") == (
        "https://media.example.test/folder/video.mp4"
    )
    oss_delivery.sdk.Bucket.assert_called_once_with(
        oss_delivery.sdk.Auth.return_value, "http://oss-cn-test.example.test", "source-bucket"
    )
    oss_delivery.bucket.sign_url.assert_called_once_with("GET", "folder/video.mp4", expected, slash_safe=True)
    oss_delivery.bucket.put_object.assert_not_called()


@pytest.mark.parametrize("uri", ["oss://", "oss://bucket", "oss://bucket/", "oss:///key"])
def test_oss_uri_rejects_missing_bucket_or_key(oss_delivery, uri):
    with pytest.raises(ValueError, match="bad oss uri"):
        read_native_av._sign_oss_uri(uri)
    oss_delivery.sdk.Bucket.assert_not_called()


@pytest.mark.parametrize("setting", ["OSS_AK", "OSS_SK", "OSS_ENDPOINT"])
def test_oss_uri_requires_standard_credentials(oss_delivery, monkeypatch, setting):
    monkeypatch.delenv(setting)
    with pytest.raises(RuntimeError, match="OSS_"):
        read_native_av._sign_oss_uri("oss://source-bucket/video.mp4")
    oss_delivery.sdk.Bucket.assert_not_called()
