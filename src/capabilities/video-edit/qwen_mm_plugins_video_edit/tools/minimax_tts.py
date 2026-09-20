"""MCP tool: synchronous text-to-speech via MiniMax."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.content import text_error
from shared.env import get_env
from shared.retry import retry_call


class MiniMaxVoiceSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    vol: float = Field(default=1.0, gt=0, le=10)
    pitch: int = Field(default=0, ge=-12, le=12)
    emotion: (
        Literal["happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent", "whisper"] | None
    ) = None


class MiniMaxAudioSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["mp3", "wav", "flac", "pcm"] = "mp3"
    sample_rate: Literal[8000, 16000, 22050, 24000, 32000, 44100] = 32000
    bitrate: Literal[32000, 64000, 128000, 256000] = 128000
    channel: Literal[1, 2] = 1


class MiniMaxVoiceModify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pitch: int | None = Field(default=None, ge=-100, le=100)
    intensity: int | None = Field(default=None, ge=-100, le=100)
    timbre: int | None = Field(default=None, ge=-100, le=100)
    sound_effects: Literal["spacious_echo", "auditorium_echo", "lofi_telephone", "robotic"] | None = None


class MiniMaxTtsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=9999, pattern=r"\S")
    voice: str = Field(pattern=r"\S")
    language_type: str = Field(default="Auto", pattern=r"\S")
    output_dir: str | None = None
    model: str = Field(default="speech-2.8-hd", pattern=r"\S")
    region: Literal["global", "cn"] = "global"
    subtitle_enable: bool = False
    voice_setting: MiniMaxVoiceSetting = Field(default=MiniMaxVoiceSetting())
    audio_setting: MiniMaxAudioSetting = Field(default=MiniMaxAudioSetting())
    pronunciation_dict: dict[str, list[str]] | None = None
    voice_modify: MiniMaxVoiceModify | None = None
    stream: Literal[False] = False
    output_format: Literal["url", "hex"] = "url"


TOOL = {"name": "minimax_tts", "args": MiniMaxTtsArgs}

_ENDPOINTS = {
    "global": "https://api.minimax.io/v1/t2a_v2",
    "cn": "https://api.minimax.cn/v1/t2a_v2",
}


def _retryable(error: Exception) -> bool:
    status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in (408, 429) or status >= 500
    return True


def _request(endpoint: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    import requests

    def post() -> dict[str, Any]:
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise TypeError("response is not a JSON object")
        return result

    return retry_call(post, attempts=3, base_backoff=1.0, mode="exp", should_retry=_retryable)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate speech with MiniMax from text and a voice ID.

    Optional voice, audio, pronunciation, and effect settings have defaults. Returns an MP3
    URL by default; pass output_dir to also save the file locally. Set subtitle_enable=true
    for an aligned subtitle URL. Requires MINIMAX_API_KEY.

    Args:
        text: Text to synthesize, fewer than 10,000 characters.
        voice: MiniMax voice ID, such as English_expressive_narrator.
        language_type: Language hint, e.g. Chinese, English, Japanese, or Auto for detection.
        output_dir: Directory to save audio. If omitted, returns a URL valid for 24 hours,
            or saves hex audio to a temporary directory.
        model: MiniMax speech model ID. Defaults to speech-2.8-hd.
        region: API region matching your MiniMax account: global or cn, independently of
            the spoken language.
        subtitle_enable: Also return aligned subtitles as a URL.
        voice_setting: Optional voice controls. speed is a multiplier from 0.5 to 2;
            vol is a multiplier above 0 and up to 10; pitch shifts by -12 to 12 semitones.
            emotion is an optional model-supported emotion. Omitted fields keep their
            defaults; select the voice with voice, not this object.
        audio_setting: Optional output format, sample_rate in Hz, MP3 bitrate in bits per
            second, and channel count. Defaults to MP3, 32 kHz, 128 kbps, mono.
        pronunciation_dict: Optional replacements, e.g. {"tone": ["Omg/Oh my god"]}.
        voice_modify: Optional pitch, intensity, and timbre effects from -100 to 100,
            plus sound_effects. Disabled by default.
        stream: Only false is supported; this tool returns non-streaming responses.
        output_format: URL by default; hex responses are decoded and saved locally.
    """
    api_key = get_env("MINIMAX_API_KEY")
    if not api_key:
        return text_error("MINIMAX_API_KEY not set")

    try:
        args = MiniMaxTtsArgs.model_validate(arguments)
    except ValidationError as error:
        detail = error.errors(include_input=False)[0]
        field = ".".join(map(str, detail["loc"]))
        return text_error(f"invalid {field}: {detail['msg']}")

    language = args.language_type.strip()
    payload = args.model_dump(exclude_none=True, exclude={"voice", "language_type", "output_dir", "region"})
    payload["voice_setting"]["voice_id"] = args.voice
    payload["language_boost"] = "auto" if language.lower() == "auto" else language

    try:
        import requests
    except ImportError:
        return text_error("missing dependency. Install with: pip install requests")

    try:
        result = _request(_ENDPOINTS[args.region], api_key, payload)
    except requests.RequestException as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        detail = f" (HTTP {status})" if isinstance(status, int) else ""
        return text_error(f"request failed{detail}: {type(error).__name__}")
    except (TypeError, ValueError) as error:
        return text_error(f"request failed: {type(error).__name__}")

    base_resp = result.get("base_resp") or {}
    if not isinstance(base_resp, dict):
        return text_error("response did not include a valid status")
    status_code = base_resp.get("status_code")
    if status_code not in (None, 0):
        return text_error(f"[{status_code}] {base_resp.get('status_msg', 'request failed')}")

    data = result.get("data")
    if not isinstance(data, dict) or data.get("status") != 2:
        return text_error("response did not include completed audio data")
    audio = data.get("audio")
    if not isinstance(audio, str) or not audio:
        return text_error("response did not include audio content")
    audio_bytes = None
    if args.output_format == "url":
        if not audio.startswith(("https://", "http://")):
            return text_error("response did not include an audio URL")
    else:
        try:
            audio_bytes = bytes.fromhex(audio)
        except ValueError:
            return text_error("response audio was not valid hexadecimal data")
        if not audio_bytes:
            return text_error("response audio was empty")

    lines = [f"**Voice**: {args.voice}", f"**Model**: {args.model}"]
    if args.output_format == "url":
        lines.insert(0, f"**Audio URL**: {audio}")
    subtitle_url = data.get("subtitle_file")
    if isinstance(subtitle_url, str) and subtitle_url:
        lines.append(f"**Subtitle URL**: {subtitle_url}")

    if args.output_dir or audio_bytes is not None:
        # Key by the result, not the prompt: repeating a synthesis can produce different audio.
        cache_key = hashlib.sha256(audio.encode()).hexdigest()[:16]
        try:
            destination = (
                Path(args.output_dir or Path(tempfile.gettempdir()) / "qwen-mm-plugins").expanduser().resolve()
            )
            audio_file = destination / f"minimax_tts_{cache_key}.{args.audio_setting.format}"
            if audio_bytes is not None:
                destination.mkdir(parents=True, exist_ok=True)
                audio_file.write_bytes(audio_bytes)
            else:
                from shared.api_dashscope import save_url_to_dir

                save_url_to_dir(audio, str(audio_file), timeout=60)
            lines.append(f"**Saved to**: {audio_file}")
        except (OSError, ValueError, requests.RequestException) as error:
            # The synthesis succeeded; retain its URLs so a failed download needs no new paid call.
            action = "Download" if audio_bytes is None else "Save"
            lines.append(f"**{action} failed**: {type(error).__name__}")

    extra_info = result.get("extra_info") or {}
    if isinstance(extra_info, dict):
        if extra_info.get("audio_length") is not None:
            lines.append(f"**Duration (ms)**: {extra_info['audio_length']}")
        if extra_info.get("usage_characters") is not None:
            lines.append(f"**Characters**: {extra_info['usage_characters']}")
    return [{"type": "text", "text": "\n".join(lines)}]
