"""Synthesize one translated utterance through the external IndexTTS2 service."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.content import text, text_error

from ..client import synthesize_speech


class SynthesizeSpeechArgs(BaseModel):
    text: str
    reference_audio: str
    output_path: str
    server: str | None = None


TOOL = {"name": "synthesize_dubbing_speech", "args": SynthesizeSpeechArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Clone the style of a local reference clip and save one translated speech WAV locally.

    Args:
        text: Translated utterance to speak. Must not be blank.
        reference_audio: Readable local audio path whose speaker style is cloned.
        output_path: Local path for the synthesized WAV; parent directories are created.
        server: Optional service URL override.
    """
    try:
        kwargs = dict(arguments)
        kwargs["explicit_server"] = kwargs.pop("server", None)
        return [text(json.dumps(synthesize_speech(**kwargs), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
