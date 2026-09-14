"""MCP tool: extract a short video clip from a source video."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import (
    CLIPS_SUBDIR,
    MediaOpsError,
    _run,
    asset_dir,
    ffmpeg_path,
    format_mmss,
    get_video_metadata,
    validate_segment,
    validate_video_path,
)


class ExtractClipArgs(BaseModel):
    video_path: str = Field()
    start: float | None = Field(default=None)
    end: float | None = Field(default=None)
    output_dir: str | None = Field(default=None)


TOOL: dict[str, Any] = {"name": "extract_clip", "args": ExtractClipArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Cut an mp4 clip from a video for bundling into the skill as an asset. Give [start, end] to cut the
    MINIMAL span that carries the asset (just-enough, no longer than the point needs); omit both to take
    the whole video. Output lands in <output_dir>/clips/<MMmSSs>_<MMmSSs>.mp4.

    Args:
        video_path: Path to the source video file.
        start: Start time in seconds. Omit BOTH start and end to grab the whole video; give both to cut just
            [start, end].
        end: End time in seconds. Omit to run to the end of the video.
        output_dir: Output directory (default: system temp).
    """
    raw_path = arguments["video_path"]
    start = arguments.get("start")
    end = arguments.get("end")
    output_dir = arguments.get("output_dir")

    path = validate_video_path(raw_path)
    out_dir = asset_dir(output_dir, CLIPS_SUBDIR)
    # Omit start/end → whole video; give both → cut [start, end].
    if end is None:
        end = get_video_metadata(str(path)).duration_sec
    start, end = validate_segment(start if start is not None else 0.0, end)

    out_path = out_dir / f"{format_mmss(start)}_{format_mmss(end)}.mp4"

    copy_cmd = [
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
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(out_path),
    ]
    try:
        _run(copy_cmd)
        produced = out_path.is_file() and out_path.stat().st_size > 0
    except MediaOpsError:
        produced = False

    if not produced:
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
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-y",
                str(out_path),
            ],
            timeout=300,
        )

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise MediaOpsError("Clip file was not produced.")

    return [{"type": "text", "text": f'{{"path": "{out_path}", "start": {start}, "end": {end}}}'}]
