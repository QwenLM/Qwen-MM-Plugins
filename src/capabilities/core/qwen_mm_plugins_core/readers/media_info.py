"""MCP tool: full media metadata via ffprobe — the complete source info an editing task needs.

Probing lives in shared.video.probe_media; this module is the media_info tool: arg model plus
a compact per-stream report (container, duration, bitrate, every video/audio/subtitle/data
stream, chapters), with the raw ffprobe JSON available on request.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from shared.content import require_file, text, text_error
from shared.video import probe_media


class MediaInfoArgs(BaseModel):
    path: str
    raw: bool = False


TOOL = {"name": "media_info", "args": MediaInfoArgs}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fmt_duration(sec: float) -> str:
    if sec <= 0:
        return "unknown duration"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    clock = f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"
    return f"{clock} ({sec:.2f}s)"


def _fmt_bitrate(v: Any) -> str | None:
    bps = _num(v)
    if bps <= 0:
        return None
    return f"{bps / 1e6:.2f} Mb/s" if bps >= 1e6 else f"{bps / 1e3:.0f} kb/s"


def _fmt_size(v: Any) -> str | None:
    b = _num(v)
    if b <= 0:
        return None
    return f"{b / 2**30:.2f} GiB" if b >= 2**30 else f"{b / 2**20:.2f} MiB"


def _parse_rate(rate: Any) -> float:
    """Parse an ffprobe rational rate like '30000/1001' (0.0 when absent/degenerate)."""
    if isinstance(rate, str) and "/" in rate:
        num, den = rate.split("/", 1)
        if _num(den) > 0 and _num(num) > 0:
            return _num(num) / _num(den)
    return _num(rate)


def _rotation(stream: dict) -> int | None:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            return int(_num(sd["rotation"]))
    rotate = (stream.get("tags") or {}).get("rotate")
    return int(_num(rotate)) if rotate else None


def _stream_tail(stream: dict) -> str:
    """Shared per-stream suffix: language tag + notable disposition flags."""
    parts = []
    tags = stream.get("tags") or {}
    if tags.get("language") and tags["language"] != "und":
        parts.append(f"lang={tags['language']}")
    if tags.get("title"):
        parts.append(f"title={tags['title']!r}")
    disp = stream.get("disposition") or {}
    flags = [k for k in ("default", "forced", "attached_pic") if disp.get(k)]
    if flags:
        parts.append(f"[{', '.join(flags)}]")
    return (", " + ", ".join(parts)) if parts else ""


def _stream_bitrate(stream: dict) -> str | None:
    # mkv/webm carry no stream bit_rate field; the muxer often stores it in a BPS tag instead.
    return _fmt_bitrate(stream.get("bit_rate") or (stream.get("tags") or {}).get("BPS"))


def _describe_video(s: dict, fallback_duration: float) -> str:
    codec = s.get("codec_name", "?")
    if s.get("profile"):
        codec += f" ({s['profile']})"
    parts = [codec, f"{s.get('width', 0)}x{s.get('height', 0)}"]
    dar = s.get("display_aspect_ratio")
    if dar and dar not in ("0:1", "N/A"):
        parts[-1] += f" [DAR {dar}]"
    if s.get("pix_fmt"):
        parts.append(s["pix_fmt"])

    r_fps = _parse_rate(s.get("r_frame_rate"))
    avg_fps = _parse_rate(s.get("avg_frame_rate"))
    if avg_fps > 0 and r_fps > 0 and abs(avg_fps - r_fps) > 0.01:
        parts.append(f"{r_fps:.2f} fps (avg {avg_fps:.2f} — possibly VFR)")
    elif r_fps > 0 or avg_fps > 0:
        parts.append(f"{(avg_fps or r_fps):.2f} fps")

    if br := _stream_bitrate(s):
        parts.append(br)

    nb_frames = int(_num(s.get("nb_frames")))
    duration = _num(s.get("duration")) or fallback_duration
    fps = avg_fps or r_fps
    if nb_frames > 0:
        parts.append(f"{nb_frames} frames")
    elif duration > 0 and fps > 0:  # mkv/webm don't store nb_frames — estimate
        parts.append(f"~{int(duration * fps)} frames")

    if (rot := _rotation(s)) is not None:
        parts.append(f"rotation {rot}°")
    if s.get("color_space") and s["color_space"] != "unknown":
        parts.append(f"color {s['color_space']}")
    return ", ".join(parts) + _stream_tail(s)


def _describe_audio(s: dict) -> str:
    codec = s.get("codec_name", "?")
    if s.get("profile"):
        codec += f" ({s['profile']})"
    parts = [codec]
    if _num(s.get("sample_rate")) > 0:
        parts.append(f"{int(_num(s['sample_rate']))} Hz")
    channels = int(_num(s.get("channels")))
    if channels > 0:
        layout = s.get("channel_layout")
        parts.append(f"{channels} ch ({layout})" if layout else f"{channels} ch")
    if s.get("sample_fmt"):
        parts.append(s["sample_fmt"])
    if br := _stream_bitrate(s):
        parts.append(br)
    if _num(s.get("duration")) > 0:
        parts.append(_fmt_duration(_num(s["duration"])))
    return ", ".join(parts) + _stream_tail(s)


def _describe_other(s: dict) -> str:
    return s.get("codec_name", s.get("codec_type", "?")) + _stream_tail(s)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Read full metadata from a video/audio file via ffprobe: container format, duration, file size,
    overall bitrate, chapters, and every stream — video (codec/profile, resolution, aspect ratio,
    fps, pixel format, bitrate, frame count, rotation, color space), audio (codec, sample rate,
    channels/layout, bitrate), subtitles, and per-stream language/disposition. Use this before any
    editing/clipping task to learn the source properties; it reads only metadata, so it is fast even
    on huge files.

    Args:
        path: Absolute path to the media file (video or audio)
        raw: Also return the raw ffprobe JSON (format + streams + chapters) for fields not covered
            by the summary. Default: false
    """
    path = arguments.get("path", "")
    if err := require_file(path):
        return err

    try:
        probe = probe_media(path)
    except Exception as e:
        return text_error(str(e))

    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []
    chapters = probe.get("chapters") or []

    duration = _num(fmt.get("duration"))
    if duration <= 0:
        duration = max((_num(s.get("duration")) for s in streams), default=0.0)

    container = [fmt.get("format_name", "?"), _fmt_duration(duration)]
    if size := _fmt_size(fmt.get("size")):
        container.append(size)
    if br := _fmt_bitrate(fmt.get("bit_rate")):
        container.append(f"overall {br}")
    start = _num(fmt.get("start_time"))
    if abs(start) > 0.01:  # a non-zero container start offset shifts every edit timestamp
        container.append(f"start_time {start:.2f}s")
    container.append(f"{len(streams)} stream(s)")

    lines = [f"Media: {path}", f"Container: {' | '.join(container)}"]
    ftags = fmt.get("tags") or {}
    tag_bits = [f"{k}={ftags[k]!r}" for k in ("title", "encoder", "creation_time") if ftags.get(k)]
    if tag_bits:
        lines.append(f"Tags: {', '.join(tag_bits)}")

    lines.append("")
    for s in streams:
        ctype = s.get("codec_type", "?")
        if ctype == "video":
            desc = _describe_video(s, duration)
        elif ctype == "audio":
            desc = _describe_audio(s)
        else:
            desc = _describe_other(s)
        lines.append(f"{ctype.capitalize()} stream #{s.get('index', '?')}: {desc}")
    if not any(s.get("codec_type") == "audio" for s in streams):
        lines.append("Audio: none (no audio stream)")

    if chapters:
        lines.append(f"Chapters ({len(chapters)}):")
        for ch in chapters:
            title = (ch.get("tags") or {}).get("title", "")
            lines.append(f"  [{_num(ch.get('start_time')):.1f}s – {_num(ch.get('end_time')):.1f}s] {title}".rstrip())

    content = [text("\n".join(lines))]
    if arguments.get("raw"):
        content.append(text(f"Raw ffprobe JSON:\n{json.dumps(probe, indent=2, ensure_ascii=False)}"))
    return content
