"""Omni Music Caption — global music analysis: genre/mood/instrument/vocal/key/tempo tags + a dense caption.

Audio-only, whole-track analysis (NO timestamps, NO per-section breakdown). Deliberately scoped to
the fields the model reports reliably — chord progressions, melodic detail, and production techniques
are intentionally excluded (they were unreliable in practice).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from ._common import json_block, run_omni, summary_block


class OmniMusicCaptionArgs(BaseModel):
    file_path: str = Field(description="Absolute path to a local music audio/video file, or an http(s)/OSS URL.")
    model: Optional[str] = Field(
        default=None,
        description="Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.5-omni-plus.",
    )
    api_key: Optional[str] = Field(default=None, description="DashScope API key (defaults to DASHSCOPE_API_KEY).")
    base_url: Optional[str] = Field(default=None, description="OpenAI-compatible base URL override.")
    dry_run: bool = Field(default=False, description="Return the request that would be sent, without calling the API.")


TOOL: dict[str, Any] = {
    "name": "omni_music_caption",
    "description": (
        "Analyze a music track and return structured tags (genre, moods, instruments, key, time "
        "signature, vocal profile) plus a dense English caption suitable as a music-generation prompt, "
        "using the Qwen-Omni model. Whole-track global analysis — NO timestamps or per-section "
        "breakdown. Extracts the audio track if given a video. "
        "An audio file that fits the endpoint's inline cap (10 MB of base64) is sent untouched; a longer "
        "one is downmixed to 16 kHz mono MP3 to fit, which costs some fidelity — pass an http(s)/OSS URL "
        "to avoid that."
    ),
    "args": OmniMusicCaptionArgs,
}

_PROMPT = (
    "You are an expert in music theory, arrangement, and audio engineering. Analyze this music audio "
    "as a WHOLE — do NOT output timestamps or a per-section breakdown. Base every field strictly on "
    "what is audible; do NOT guess chord progressions, melodic detail, or production techniques, and "
    "never include song titles or artist names. Output STRICTLY the following JSON and nothing else. "
    "All string values in lowercase English (has_vocals is boolean):\n"
    "{\n"
    '  "genre": [2-5 tags, broad genre -> sub-genre -> micro-style; only tags clearly supported by the audio],\n'
    '  "moods": [3-6 precise mood tags with an audible basis; drop near-duplicates],\n'
    '  "instruments": [generic instrument/source names, no modifiers, e.g. "electric piano", "drum machine", '
    '"synthesizer"; cover every identifiable source],\n'
    '  "has_vocals": true or false,\n'
    '  "vocal_language": language in english (e.g. "chinese", "english", "mixed"); "none" if instrumental,\n'
    '  "vocal_gender": "male" | "female" | "mixed" | "none",\n'
    '  "vocal_timbre": [2-5 steady-state timbre descriptors, e.g. raspy/breathy/warm/smooth/full/nasal/airy; '
    "[] if no vocals],\n"
    '  "key": e.g. "f# major" / "eb minor"; "unknown" if not clearly determinable,\n'
    '  "time_signature": e.g. "4/4" / "6/8",\n'
    '  "caption": a flowing 2-4 sentence English paragraph (~60-120 words) usable as a music-generation '
    "prompt, integrating style/era, tempo and groove, main instruments and arrangement, vocals (if any, "
    "with timbre and technique), overall mood and energy, and the most salient production/space feel; "
    "prose only, no bullet lists, no song/artist names\n"
    "}"
)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    data, blocks = run_omni(arguments, prompt=_PROMPT, mode="audio")
    if blocks is not None:
        return blocks

    if not isinstance(data, dict):
        # Model returned something unexpected (list/str) — surface it rather than dropping it.
        data = {"caption": data if isinstance(data, str) else "", "raw": data}

    caption = str(data.get("caption") or "").strip()
    return [json_block(data), summary_block(caption or "(music analysis returned; no caption field)")]
