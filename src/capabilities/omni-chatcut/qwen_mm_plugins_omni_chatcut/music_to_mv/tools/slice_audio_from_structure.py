"""Structure-aware audio slicing as an atomic MCP tool."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from shared.content import text, text_error

from ..slicing import slice_audio_from_structure


class SliceAudioArgs(BaseModel):
    audio_path: str
    structure_text: str
    output_dir: str
    output_format: Literal["wav", "mp3", "flac"] = "wav"
    overwrite: bool = False


TOOL = {"name": "slice_audio_from_structure", "args": SliceAudioArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a contiguous timestamped music structure, merge sub-second micro-sections, and cut one exact audio clip per resulting section with ffmpeg. Returns the persisted segment manifest.

    Args:
        audio_path: Readable local source audio path.
        structure_text: Timestamped structure lines such as [00:00,000 --> 00:12,500] [intro] "".
        output_dir: New or empty directory that will receive clips and segments.json.
        output_format: Clip audio format.
        overwrite: Allow replacing existing clips and manifest files.
    """
    try:
        result = slice_audio_from_structure(
            audio_path=arguments.get("audio_path", ""),
            structure_text=arguments.get("structure_text", ""),
            output_dir=arguments.get("output_dir", ""),
            output_format=arguments.get("output_format", "wav"),
            overwrite=bool(arguments.get("overwrite", False)),
        )
        return [text(json.dumps(result, ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001 — tool errors are returned to the MCP caller
        return text_error(str(exc))
