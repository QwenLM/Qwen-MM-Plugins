#!/usr/bin/env python3
"""Validate an SRT lyric timeline and burn it into an MV with modern white type."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

TIME_RE = re.compile(r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})$")
TIMING_RE = re.compile(r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})$")


@dataclass(frozen=True)
class Cue:
    index: int
    start_ms: int
    end_ms: int
    lines: tuple[str, ...]


def fail(message: str) -> None:
    raise SystemExit(message)


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True, cwd=cwd)


def parse_clock(value: str) -> int:
    match = TIME_RE.fullmatch(value)
    if not match:
        fail(f"Invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, millis = (int(match.group(k)) for k in ("h", "m", "s", "ms"))
    if minutes >= 60 or seconds >= 60:
        fail(f"Invalid SRT timestamp: {value!r}")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(path: Path) -> list[Cue]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        fail("SRT is empty")
    blocks = re.split(r"\n{2,}", text)
    cues: list[Cue] = []
    previous_end = -1
    for expected_index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3:
            fail(f"SRT cue {expected_index} must contain index, timing, and lyrics")
        try:
            index = int(lines[0].strip())
        except ValueError as exc:
            raise SystemExit(f"Invalid SRT cue index: {lines[0]!r}") from exc
        if index != expected_index:
            fail(f"SRT cue indices must be continuous from 1; expected {expected_index}, got {index}")
        timing = TIMING_RE.fullmatch(lines[1].strip())
        if not timing:
            fail(f"Invalid timing line in cue {index}: {lines[1]!r}")
        start_ms = parse_clock(timing.group(1))
        end_ms = parse_clock(timing.group(2))
        lyric_lines = tuple(line.strip() for line in lines[2:] if line.strip())
        if not lyric_lines:
            fail(f"Cue {index} has no lyric text")
        if start_ms >= end_ms:
            fail(f"Cue {index} must start before it ends")
        if start_ms < previous_end:
            fail(f"Cue {index} overlaps the previous cue")
        cues.append(Cue(index, start_ms, end_ms, lyric_lines))
        previous_end = end_ms
    return cues


def probe(path: Path) -> dict:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def video_stream(probe_data: dict) -> dict:
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    fail("Input has no video stream")
    raise AssertionError


def has_audio(probe_data: dict) -> bool:
    return any(stream.get("codec_type") == "audio" for stream in probe_data.get("streams", []))


def media_duration(probe_data: dict) -> float:
    try:
        return float(probe_data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("Could not determine media duration") from exc


def ass_time(milliseconds: int) -> str:
    centiseconds = int(round(milliseconds / 10))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def escape_ass_text(lines: tuple[str, ...]) -> str:
    escaped = []
    for line in lines:
        escaped.append(line.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}"))
    return r"\N".join(escaped)


def make_ass(cues: list[Cue], width: int, height: int) -> str:
    font_size = max(36, round(height * 62 / 1080))
    margin_v = max(42, round(height * 76 / 1080))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyrics,Noto Sans CJK SC,{font_size},&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,1,0,0,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        text = escape_ass_text(cue.lines)
        events.append(
            f"Dialogue: 0,{ass_time(cue.start_ms)},{ass_time(cue.end_ms)},Lyrics,,0,0,0,,"
            rf"{{\fad(120,180)}}{text}"
        )
    return header + "\n".join(events) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--srt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--font", type=Path, help="Override bundled Noto Sans CJK SC Bold font")
    parser.add_argument("--ass-output", type=Path, help="ASS artifact path (default: output with .ass suffix)")
    parser.add_argument("--preset", default="slow")
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            fail(f"Required executable not found on PATH: {executable}")
    video = args.video.expanduser().resolve()
    srt = args.srt.expanduser().resolve()
    output = args.output.expanduser().resolve()
    font = (
        args.font.expanduser().resolve()
        if args.font
        else (Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansCJKsc-Bold.otf")
    )
    ass_output = args.ass_output.expanduser().resolve() if args.ass_output else output.with_suffix(".ass")
    if not video.is_file():
        fail(f"Video does not exist: {video}")
    if not srt.is_file():
        fail(f"SRT does not exist: {srt}")
    if not font.is_file():
        fail(f"Font does not exist: {font}")
    if output == video:
        fail("Output must differ from input video")
    for candidate in (output, ass_output):
        if candidate.exists() and not args.overwrite:
            fail(f"Refusing to overwrite existing artifact: {candidate}")

    cues = parse_srt(srt)
    input_probe = probe(video)
    stream = video_stream(input_probe)
    if not has_audio(input_probe):
        fail("Completed MV must contain its final audio track")
    width = int(stream["width"])
    height = int(stream["height"])
    duration = media_duration(input_probe)
    if cues[-1].end_ms > round((duration + 0.10) * 1000):
        fail(f"Last cue ends at {cues[-1].end_ms / 1000:.3f}s, beyond video duration {duration:.3f}s")

    output.parent.mkdir(parents=True, exist_ok=True)
    ass_output.parent.mkdir(parents=True, exist_ok=True)
    ass_output.write_text(make_ass(cues, width, height), encoding="utf-8")

    overwrite_flag = "-y" if args.overwrite else "-n"
    with tempfile.TemporaryDirectory(prefix="mv_subtitles_") as temp_name:
        temp_dir = Path(temp_name)
        shutil.copy2(ass_output, temp_dir / "lyrics.ass")
        shutil.copy2(font, temp_dir / "NotoSansCJKsc-Bold.otf")
        command = [
            "ffmpeg",
            "-v",
            "error",
            overwrite_flag,
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            # Name the first option explicitly: a current ffmpeg rejects a filter that mixes
            # positional shorthand with named options ("No option name near ...").
            "ass=filename=lyrics.ass:fontsdir=.",
            "-c:v",
            "libx264",
            "-preset",
            args.preset,
            "-crf",
            str(args.crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            run(command, cwd=temp_dir)
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or "ffmpeg failed").strip()
            fail(details)

    output_probe = probe(output)
    output_stream = video_stream(output_probe)
    output_duration = media_duration(output_probe)
    if not has_audio(output_probe):
        fail("Rendered output lost the audio track")
    if (int(output_stream["width"]), int(output_stream["height"])) != (width, height):
        fail("Rendered output resolution changed unexpectedly")
    if abs(output_duration - duration) > max(0.25, duration * 0.01):
        fail("Rendered output duration changed unexpectedly")
    try:
        run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"])
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "full decode failed").strip()
        fail(details)

    result = {
        "schema_version": "mv-lyrics-render.v1",
        "video_path": str(video),
        "srt_path": str(srt),
        "output_path": str(output),
        "ass_path": str(ass_output),
        "font_path": str(font),
        "resolution": {"width": width, "height": height},
        "duration": round(output_duration, 6),
        "cue_count": len(cues),
        "validation": "passed",
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
