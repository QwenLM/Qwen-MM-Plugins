"""Detect speech intervals through the external TEN-VAD service."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from shared.content import text, text_error

from ..client import detect_speech


class DetectSpeechArgs(BaseModel):
    input_path: str
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    hop_size: int = Field(default=256, ge=80, le=1024)
    min_speech: float = Field(default=0.2, ge=0.0, le=10.0)
    min_silence: float = Field(default=0.3, ge=0.0, le=10.0)
    pad: float = Field(default=0.1, ge=0.0, le=2.0)
    server: str | None = None


TOOL = {"name": "detect_dubbing_speech", "args": DetectSpeechArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect speech intervals in local audio using the configured TEN-VAD service.

    Args:
        input_path: Readable local audio path, preferably an already separated vocals track.
        threshold: Speech probability above which a frame counts as speech, from 0 to 1. Raise it to keep only
            confident speech, lower it to catch quiet delivery.
        hop_size: VAD analysis hop in samples, from 80 to 1024. Smaller hops give finer interval boundaries at
            more compute.
        min_speech: Shortest speech run to report, in seconds. Shorter runs are discarded as noise.
        min_silence: Shortest silence that splits neighbouring intervals, in seconds. Briefer gaps are merged
            into one interval.
        pad: Seconds of padding added to each side of a detected interval so onsets and tails are not clipped.
        server: Optional service URL override.
    """
    try:
        kwargs = dict(arguments)
        input_path = kwargs.pop("input_path")
        kwargs["explicit_server"] = kwargs.pop("server", None)
        return [text(json.dumps(detect_speech(input_path, **kwargs), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
