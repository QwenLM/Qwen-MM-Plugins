"""Qwen-MM-Plugins api: model services for understanding media, grouped by model family.

A pure-tools MCP server. Each module under ``vl/`` / ``omni/`` / ``others/`` exports ``TOOL`` +
``handle`` and is auto-discovered by the framework:

* ``vl/`` — a Qwen-VL model (OpenAI-compatible endpoint): vision_chat, ocr, grounding.
* ``omni/`` — Qwen-Omni audio transcription/analysis and joint video/audio understanding:
  omni_asr(+_timestamped / multi_speaker), omni_av_caption / grounding / counting, omni_music_caption.
* ``others/`` — services that are neither: transcribe_audio (Qwen3-ASR) and segmentation (SAM3).

Local file reading/visualization lives in ``core``; fact-finding/confirmation lives in ``search``.
"""

from mcp_framework import build_registry

__version__ = "1.1.0"

# Auto-discover tools from the three model-family subpackages.
SPECS, get_handler, list_tools = build_registry(__name__, ["vl", "omni", "others"])

# System tools pip/uv cannot install; the framework renders --check-system + startup warnings from
# this table. ffmpeg fits every local file to the endpoint's inline cap: transcribe_audio pulls out
# the audio track, and the Omni A/V tools transcode the video (splitting it into frames when it is
# too long to inline).
SYSTEM_DEPS = [
    {
        "label": "audio/video decoding (transcribe_audio track extraction; Omni inline-size fitting + frame split)",
        "tools": ["ffmpeg", "ffprobe"],
        "hint": "apt install ffmpeg   |   brew install ffmpeg",
        "extra": "api",
        "probe": "openai",
    },
]

USAGE_NOTE = (
    "Media-understanding model services (model defaults per family). "
    "VL model: vision_chat (caption/VQA), ocr, grounding. "
    "Omni model: omni_asr / omni_asr_timestamped / omni_multi_speaker_asr, "
    "omni_av_caption / omni_av_grounding / omni_av_counting, omni_music_caption. "
    "VL/Omni default to DashScope; compatible endpoints can be configured with base_url/api_key/model. "
    "Others: transcribe_audio (DashScope Qwen3-ASR with ASR_SERVER_URLS fallback), "
    "segmentation (SAM3_SERVER_URL or the server argument). "
    "VL uploads local video when OSS and duration limits allow; otherwise it samples inline frames. "
    "Omni first fits local media inline, then uses OSS or frames plus audio when needed. "
    "Read/visualize local files with qwen-mm-plugins-core; find/confirm facts with qwen-mm-plugins-search."
)
