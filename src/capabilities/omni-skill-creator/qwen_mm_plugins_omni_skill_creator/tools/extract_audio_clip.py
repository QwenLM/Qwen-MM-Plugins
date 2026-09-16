"""MCP tool: extract an audio clip (mp3) from a source video."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import (
    AUDIO_SUBDIR,
    MediaOpsError,
    _run,
    asset_dir,
    ffmpeg_path,
    format_mmss,
    get_video_metadata,
    validate_segment,
    validate_video_path,
)


class ExtractAudioClipArgs(BaseModel):
    video_path: str = Field()
    start: float | None = Field(default=None)
    end: float | None = Field(default=None)
    output_dir: str | None = Field(default=None)


TOOL: dict[str, Any] = {"name": "extract_audio_clip", "args": ExtractAudioClipArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Extract an mp3 audio segment from a video. Omit start/end to grab the WHOLE audio track (e.g. to
    transcribe it); give both to cut just that span. When bundling as a skill asset, keep it minimal —
    no longer than the point needs. Output lands in <output_dir>/audio/<MMmSSs>_<MMmSSs>.mp3.

    Args:
        video_path: Path to the source video file.
        start: Start time in seconds. Omit BOTH start and end to extract the whole audio track; give both to cut
            just [start, end].
        end: End time in seconds. Omit to run to the end of the audio.
        output_dir: Output directory (default: system temp).
    """
    raw_path = arguments["video_path"]
    start = arguments.get("start")
    end = arguments.get("end")
    output_dir = arguments.get("output_dir")

    path = validate_video_path(raw_path)
    out_dir = asset_dir(output_dir, AUDIO_SUBDIR)

    meta = get_video_metadata(str(path))
    if not meta.has_audio:
        raise MediaOpsError("The video has no audio stream.")

    # Omit start/end → whole track; give both → cut [start, end].
    start, end = validate_segment(
        start if start is not None else 0.0,
        end if end is not None else meta.duration_sec,
    )

    stamp = f"{format_mmss(start)}_{format_mmss(end)}"
    audio_path = out_dir / f"{stamp}.mp3"
    _run(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(path),
            "-vn",
            "-q:a",
            "4",
            "-y",
            str(audio_path),
        ]
    )
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise MediaOpsError("Audio file was not produced.")

    return [{"type": "text", "text": f'{{"path": "{audio_path}", "start": {start}, "end": {end}}}'}]
