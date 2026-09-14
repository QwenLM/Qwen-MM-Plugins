"""Grounding uses the shared VL transport while retaining creator's pixel/annotation contract."""

import base64
import io
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from PIL import Image, ImageDraw

from qwen_mm_plugins_omni_skill_creator.tools import grounding, image_annotate
from qwen_mm_plugins_omni_skill_creator.tools._media_utils import MediaOpsError
from shared import env

_SETTINGS = (
    "DASHSCOPE_BASE_URL",
    "DASHSCOPE_API_KEY",
    "QWEN_MM_API_VL_MODEL",
    "QWEN_MM_CHAT_TIMEOUT",
    "ORCAROUTER_API_KEY",
    "OPENROUTER_API_KEY",
    "OMNI_API_KEY",
    "API_KEY",
    "OMNI_GROUNDING_BASE_URL",
    "OMNI_GROUNDING_MODEL",
    "OMNI_GROUNDING_TIMEOUT",
)


@pytest.fixture
def vl_backend(monkeypatch):
    for name in _SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env, "_config_cache", {})
    monkeypatch.setattr("shared.retry.time.sleep", lambda *_: None)
    backend = SimpleNamespace(
        requests=[], clients=[], statuses=[], text='[{"label":"red card","bbox":[250,250,750,750]}]'
    )

    def respond(request):
        backend.requests.append(request)
        status = backend.statuses.pop(0) if backend.statuses else 200
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "synthetic failure", "type": "test"}})
        return httpx.Response(
            200,
            json={
                "id": "test-grounding",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": backend.text}, "finish_reason": "stop"}
                ],
            },
        )

    real_client = openai.OpenAI

    def client(**kwargs):
        backend.clients.append(kwargs)
        # Exercise the real SDK/serialization. Retry assertions concern shared.retry, not SDK retries.
        return real_client(**kwargs, max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(openai, "OpenAI", client)
    return backend


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "sample.png"
    Image.new("RGB", (80, 40), "white").save(path)
    return path


def call_grounding(image, **kwargs):
    return json.loads(grounding.handle({"image_path": str(image), "target": "the red card", **kwargs})[0]["text"])


def test_shared_config_file_controls_model_endpoint_and_timeout(vl_backend, sample, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text(
        "DASHSCOPE_BASE_URL=https://openrouter.ai/api/v1\n"
        "OPENROUTER_API_KEY=router-test-key\nQWEN_MM_API_VL_MODEL=config-vl\nQWEN_MM_CHAT_TIMEOUT=37\n"
    )
    monkeypatch.setattr(env, "config_file", lambda: str(config))
    monkeypatch.setattr(env, "_config_cache", None)
    for key, value in {
        "OMNI_GROUNDING_MODEL": "obsolete-model",
        "OMNI_GROUNDING_BASE_URL": "https://obsolete.invalid",
        "OMNI_GROUNDING_TIMEOUT": "1",
        "OMNI_API_KEY": "obsolete-key",
        "API_KEY": "generic-key",
        "DASHSCOPE_API_KEY": "wrong-provider-key",
    }.items():
        monkeypatch.setenv(key, value)

    result = call_grounding(sample)
    request = vl_backend.requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer router-test-key"
    assert payload["model"] == result["model"] == "config-vl"
    assert vl_backend.clients[0]["timeout"] == 37
    assert payload["max_tokens"] == 2048
    assert payload["enable_thinking"] is False
    assert payload["messages"][0]["content"][1]["text"] == grounding.PROMPT.format(target="the red card")
    assert result["target"] == "the red card"
    assert result["size"] == {"w": 80, "h": 40}
    assert result["detections"][0]["bbox_px"] == [20, 10, 60, 30]
    assert result["detections"][0]["bbox_norm"] == [250, 250, 750, 750]


@pytest.mark.parametrize(
    "base,key_env",
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
        ("https://api.orcarouter.ai/v1", "ORCAROUTER_API_KEY"),
    ],
)
def test_provider_key_is_selected_from_endpoint(vl_backend, sample, monkeypatch, base, key_env):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", base)
    monkeypatch.setenv(key_env, "selected-key")
    monkeypatch.setenv("OMNI_API_KEY", "ignored-legacy-key")
    call_grounding(sample)
    assert vl_backend.requests[0].headers["authorization"] == "Bearer selected-key"


def test_explicit_overrides_and_custom_server_without_auth(vl_backend, sample, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "must-not-leak-to-custom-host")
    monkeypatch.setenv("QWEN_MM_API_VL_MODEL", "environment-model")
    result = call_grounding(
        sample, model="explicit-model", base_url="https://custom.example/v1", api_key="explicit-key"
    )
    assert result["model"] == "explicit-model"
    assert str(vl_backend.requests[-1].url) == "https://custom.example/v1/chat/completions"
    assert vl_backend.requests[-1].headers["authorization"] == "Bearer explicit-key"
    call_grounding(sample, base_url="https://custom.example/v1")
    assert vl_backend.requests[-1].headers["authorization"] == "Bearer EMPTY"


@pytest.mark.parametrize("status", [429, 500])
def test_transient_failures_use_shared_retry(vl_backend, sample, status):
    vl_backend.statuses = [status, 200]
    assert call_grounding(sample)["detections"]
    assert len(vl_backend.requests) == 2


@pytest.mark.parametrize("status", [400, 422])
def test_unsupported_thinking_hint_is_removed_before_retry(vl_backend, sample, status):
    vl_backend.statuses = [status, 200]
    assert call_grounding(sample)["detections"]
    first, second = [json.loads(r.content) for r in vl_backend.requests]
    assert first.pop("enable_thinking") is False
    assert "enable_thinking" not in second
    assert first == second


def test_authentication_failure_is_not_retried_or_turned_into_boxes(vl_backend, sample):
    vl_backend.statuses = [401]
    with pytest.raises(MediaOpsError, match="AuthenticationError"):
        call_grounding(sample)
    assert len(vl_backend.requests) == 1


def test_exif_coordinates_still_feed_image_annotate(vl_backend, tmp_path):
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (80, 40), "white")
    ImageDraw.Draw(image).rectangle((10, 5, 30, 25), fill="blue")
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    vl_backend.text = '[{"label":"blue card","bbox":[375,125,875,375]}]'
    result = call_grounding(path)
    payload = json.loads(vl_backend.requests[0].content)
    data_url = payload["messages"][0]["content"][0]["image_url"]["url"]
    sent = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    assert sent.size == (40, 80)
    assert sent.getexif().get(274, 1) == 1
    assert result["size"] == {"w": 40, "h": 80}
    box = result["detections"][0]["bbox_px"]
    assert box == [15, 10, 35, 30]

    blocks = image_annotate.handle(
        {
            "image_path": str(path),
            "annotations": [{"type": "box", "bbox": box}],
            "coord_space": "pixel",
            "output_dir": str(tmp_path / "output"),
        }
    )
    metadata = json.loads(blocks[0]["text"])
    annotated = Image.open(metadata["path"])
    assert annotated.size == (40, 80)
    assert metadata["coord_space"] == "pixel"
    assert any(block["type"] == "image" for block in blocks)
    assert annotated.getpixel((15, 10)) != sent.convert("RGB").getpixel((15, 10))


def test_pixel_warning_and_malformed_quote_recovery_are_preserved(vl_backend, tmp_path):
    image = tmp_path / "wide.png"
    Image.new("RGB", (1920, 1080), "white").save(image)
    vl_backend.text = '[{"label":"button","bbox":[1039,124,1115,186"]}]'
    result = call_grounding(image)
    assert result["coordinates_returned_by_model"] == "pixel"
    assert result["detections"][0]["bbox_px"] == [1039, 124, 1115, 186]
    assert result["detections"][0]["bbox_norm"] is None
    assert "image_annotate" in result["coordinate_warning"]


def test_empty_result_keeps_diagnostics(vl_backend, sample):
    vl_backend.text = "[]"
    result = call_grounding(sample)
    assert result["detections"] == []
    assert result["raw_response"] == "[]"
    assert "No box parsed" in result["hint"]


def test_invalid_input_stops_before_api(vl_backend, sample):
    with pytest.raises(MediaOpsError, match="target"):
        call_grounding(sample, target=" ")
    with pytest.raises(MediaOpsError, match="not found"):
        call_grounding(sample.with_name("missing.png"))
    assert not vl_backend.requests
