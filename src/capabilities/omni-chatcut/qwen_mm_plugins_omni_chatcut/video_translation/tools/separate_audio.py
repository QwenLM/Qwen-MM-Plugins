"""Separate vocals and background through the external dubbing service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared.content import text, text_error

from ..client import separate_audio
from ..project import file_sha256, write_json


class SeparateAudioArgs(BaseModel):
    input_path: str
    output_dir: str
    model: str = "htdemucs"
    server: str | None = None


TOOL = {"name": "separate_dubbing_audio", "args": SeparateAudioArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Upload local audio to the configured Demucs service, save vocals/background stems locally, and write a signature that render_video_translation can reuse.

    Args:
        input_path: Readable local audio path.
        output_dir: Local directory for vocals.wav and no_vocals.wav.
        model: Demucs separation model name.
        server: Optional service URL override.
    """
    try:
        input_path = Path(arguments["input_path"]).expanduser().resolve()
        output_dir = Path(arguments["output_dir"]).expanduser().resolve()
        model = arguments.get("model", "htdemucs")
        result = separate_audio(
            str(input_path),
            str(output_dir),
            explicit_server=arguments.get("server"),
            model=model,
        )
        signature_path = output_dir / "separation.signature.json"
        write_json(signature_path, {"source_audio_sha256": file_sha256(input_path), "model": model})
        result["signature_path"] = str(signature_path)
        return [text(json.dumps(result, ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
