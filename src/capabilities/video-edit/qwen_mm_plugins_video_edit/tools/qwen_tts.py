"""MCP tool: text-to-speech via Qwen3-TTS-Flash (DashScope MultiModalConversation)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from shared.content import text_error
from shared.env import get_env


class QwenTtsArgs(BaseModel):
    text: str
    voice: str = "Cherry"
    language_type: str = "Auto"
    output_dir: Optional[str] = None


TOOL = {"name": "qwen_tts", "args": QwenTtsArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Text-to-speech (TTS) using Qwen3-TTS-Flash via the DashScope SDK
    (dashscope.MultiModalConversation). Supports 10 languages and 44 system voices. Input: text +
    voice + language. Output: audio file URL (WAV, 24h validity). Max 512 tokens per call — split
    long text at sentence boundaries.

    Args:
        text: Text to synthesize into speech. Max ~512 tokens per call. For longer text, split at
            sentence/punctuation boundaries and call multiple times.
        voice: Voice name. System voices include: Cherry (young female, energetic), Serena (female,
            professional), Ethan (male, standard), Chelsie (female, warm), and 40+ others. See
            DashScope docs for full list.
        language_type: Language for synthesis. Options: Chinese, English, Japanese, Korean, French,
            German, Spanish, Russian, Portuguese, Auto. Specifying language significantly improves
            quality. Default: Auto.
        output_dir: Directory to save the audio file. If provided, downloads the audio to this
            directory. If omitted, returns the audio URL only (valid 24h).
    """
    text = arguments["text"]
    voice = arguments.get("voice", "Cherry")
    language_type = arguments.get("language_type", "Auto")
    output_dir = arguments.get("output_dir")

    api_key = get_env("DASHSCOPE_API_KEY")
    if not api_key:
        return text_error("DASHSCOPE_API_KEY not set")

    try:
        import dashscope
    except ImportError:
        return text_error("missing dependency. Install with: pip install dashscope")

    from shared.api_dashscope import API_V1, retry_call

    try:
        dashscope.base_http_api_url = API_V1

        response = retry_call(
            dashscope.MultiModalConversation.call,
            model="qwen3-tts-flash",
            api_key=api_key,
            text=text,
            voice=voice,
            language_type=language_type,
        )

        if response.status_code != 200:
            return text_error(f"[{response.code}] {response.message}")

        audio_url = response.output["audio"]["url"]
        expires_at = response.output["audio"].get("expires_at", "")
        characters = response.usage.get("characters", 0) if response.usage else 0

        lines = [
            f"**Audio URL**: {audio_url}",
            f"**Voice**: {voice}",
            f"**Language**: {language_type}",
            f"**Characters**: {characters}",
        ]
        if expires_at:
            lines.append(f"**Expires**: {expires_at}")

        if output_dir:
            from shared.api_dashscope import save_url_to_dir

            out_path = Path(output_dir).expanduser().resolve()
            cache_key = hashlib.sha256(f"{text}|{voice}|{language_type}".encode()).hexdigest()[:16]
            audio_file = out_path / f"tts_{cache_key}.wav"
            save_url_to_dir(audio_url, str(audio_file), timeout=60)
            lines.append(f"**Saved to**: {audio_file}")

        return [{"type": "text", "text": "\n".join(lines)}]

    except Exception as e:
        return text_error(f"{e}")
