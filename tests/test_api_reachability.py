"""Live API *reachability* checks — opt-in, credential-gated.

Unlike test_api_tools.py (fully mocked, always runs), these actually hit the real
services to confirm the endpoint + credentials + model are reachable end-to-end.
They require an explicit opt-in in addition to the relevant key, so a plain
`pytest tests/` stays offline even on a configured developer machine.

Run them explicitly with real creds:
    QWEN_MM_RUN_REACHABILITY=1 DASHSCOPE_API_KEY=... \
        QWEN_MM_SEARCH_BACKEND=exa EXA_API_KEY=... \
        pytest -m reachability tests/
Skip them even when keys are present:
    pytest -m "not reachability" tests/

Assertions are deliberately loose: we check the call round-trips without a
credential/connectivity error, NOT that the model returns any specific content
(that would be flaky). The key resolution mirrors the tools themselves by going
through shared.env.get_env, so a key from env or config is honoured the same way.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import mcp_call

from qwen_mm_plugins_search.backends import BACKEND_KEY_ENVS, resolve_backend
from shared.env import get_env

pytestmark = pytest.mark.reachability

RUN_REACHABILITY = os.environ.get("QWEN_MM_RUN_REACHABILITY") == "1"
HAS_DASHSCOPE = bool(get_env("DASHSCOPE_API_KEY"))
SEARCH_BACKEND = resolve_backend()
SEARCH_KEY_ENV = BACKEND_KEY_ENVS.get(SEARCH_BACKEND)
HAS_SEARCH_KEY = bool(SEARCH_KEY_ENV and get_env(SEARCH_KEY_ENV))
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

requires_dashscope = pytest.mark.skipif(
    not RUN_REACHABILITY or not HAS_DASHSCOPE,
    reason="set QWEN_MM_RUN_REACHABILITY=1 and DASHSCOPE_API_KEY to run live checks",
)
requires_search = pytest.mark.skipif(
    not RUN_REACHABILITY or not HAS_SEARCH_KEY,
    reason=(
        "set QWEN_MM_RUN_REACHABILITY=1 and a search API key; optionally set QWEN_MM_SEARCH_BACKEND to pin a provider"
    ),
)


def _text(blocks) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _assert_reached(blocks):
    """A tool round-tripped if it returned blocks and didn't fail on credentials/connectivity."""
    assert isinstance(blocks, list) and blocks, "handler returned no content"
    txt = _text(blocks)
    lowered = txt.lower()
    for bad in ("no api key", "cannot connect", "connection error", "invalid api", "no api-key"):
        assert bad not in lowered, f"reachability failed: {txt[:200]}"
    return txt


# ── DashScope (vision_chat / ocr / grounding / transcribe_audio) ──────


@requires_dashscope
def test_vision_chat_reachable(sample_image):
    from qwen_mm_plugins_api.vl import vision_chat

    blocks = vision_chat.handle({"images": [sample_image], "text": "Reply with the single word: OK", "max_tokens": 32})
    txt = _assert_reached(blocks)
    assert '"choices"' in txt  # the raw completion payload came back


@requires_dashscope
def test_ocr_reachable(tmp_path):
    from PIL import Image, ImageDraw

    from qwen_mm_plugins_api.vl import ocr

    img = Image.new("RGB", (320, 120), "white")
    ImageDraw.Draw(img).text((20, 40), "HELLO 12345", fill="black")
    p = tmp_path / "text.png"
    img.save(p)

    blocks = ocr.handle({"image_path": str(p)})
    txt = _assert_reached(blocks)
    assert txt.strip()  # got *some* transcription back (content itself not asserted — bitmap font)


@requires_dashscope
def test_grounding_reachable(sample_image):
    import json

    from qwen_mm_plugins_api.vl import grounding

    blocks = grounding.handle({"image_path": sample_image, "prompt": "the colored regions"})
    txt = _assert_reached(blocks)
    result = json.loads(txt)  # handler emits a JSON result block
    assert "detections" in result  # may be empty; we only assert the round-trip + shape


@requires_dashscope
def test_native_mode_pdf_caption_reachable(server_dir):
    """Real PDF renderer → MCP stdio → paid VL caption → text-only MCP result."""
    pytest.importorskip("pypdfium2")
    pdf = Path(__file__).parent / "assets" / "sample.pdf"
    env = {**os.environ, "QWEN_MM_NATIVE_MODE": "0"}

    result = mcp_call(
        server_dir,
        lambda session: session.call_tool(
            "visualize",
            {
                "file_path": str(pdf),
                "pages": "1",
                "budget": "small",
                "max_pages": 1,
            },
        ),
        env=env,
    )

    assert not result.isError
    assert result.content and all(block.type == "text" for block in result.content)
    text = "\n".join(block.text for block in result.content)
    assert "[PDF Start]" in text
    assert "[Generated visual caption]" in text
    assert "[Visual content unavailable:" not in text


@requires_dashscope
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_transcribe_audio_reachable(tmp_path):
    from qwen_mm_plugins_api.others import asr

    wav = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ar", "16000", "-ac", "1", str(wav)],
        check=True,
        capture_output=True,
    )
    # A pure tone has no speech → the pipeline (ffmpeg extract + DashScope ASR) still runs
    # and returns a non-error result; we only assert it reached the service.
    blocks = asr.handle({"file_path": str(wav), "format": "text"})
    _assert_reached(blocks)


# ── Selected search backend (web_search) ─────────────────────────────


@requires_search
def test_web_search_reachable():
    from qwen_mm_plugins_search.tools import web_search

    blocks = web_search.handle({"queries": ["Alibaba Qwen"]})
    txt = _assert_reached(blocks)
    assert "http" in txt  # at least one result URL rendered
