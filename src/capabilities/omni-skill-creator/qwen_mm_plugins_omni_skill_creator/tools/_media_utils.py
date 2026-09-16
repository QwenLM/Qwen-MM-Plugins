"""Internal utilities shared by media tools (not an MCP tool — no TOOL export)."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shared.env import get_env

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi"}

MAX_KEYFRAMES = 64
# Advisory-only span thresholds. extract_clip/extract_audio_clip no longer enforce these as hard
# caps; validate_skill check #28 references them to WARN (never block) when a bundled asset runs
# longer than a point usually needs — the single source of truth for those soft limits.
MAX_CLIP_SECONDS = 15.0
MAX_AUDIO_CLIP_SECONDS = 30.0
MAX_DEDUP_CANDIDATES = 64
MAX_STORYBOARD_TILES = 16
MAX_OCR_FRAMES = 32

_PTS_TIME_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_LANGS_RE = re.compile(r"^[A-Za-z_+]+$")

FRAMES_SUBDIR = "frames"
CLIPS_SUBDIR = "clips"
AUDIO_SUBDIR = "audio"


class MediaOpsError(Exception):
    """User-facing validation and subprocess failures."""


def _env(name: str, default: str) -> str:
    # get_env: 环境变量 > ~/.qwen-mm-plugins/config(configure 写入的值经这里才读得到) > default
    return get_env(name, default) or default


def ffmpeg_path() -> str:
    return _env("FFMPEG_PATH", "ffmpeg")


def ffprobe_path() -> str:
    return _env("FFPROBE_PATH", "ffprobe")


_FILTER_CACHE: dict[str, bool] = {}


def has_filter(name: str) -> bool:
    """True if this ffmpeg build registers the named filter.

    FFmpeg 7.0 made libharfbuzz a hard build dependency of `drawtext`, so widely used static
    builds (e.g. johnvansickle 7.x) ship *without* it even though they advertise
    `--enable-libfreetype`. Probing once beats paying a failed render per call to find out.
    """
    cached = _FILTER_CACHE.get(name)
    if cached is not None:
        return cached
    found = False
    try:
        proc = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        # Lines look like " T.C drawtext V->V  Draw text ..." — match the name as its own column.
        found = re.search(rf"^\s*\S+\s+{re.escape(name)}\s", proc.stdout or "", re.MULTILINE) is not None
    except (OSError, subprocess.SubprocessError):
        found = False
    _FILTER_CACHE[name] = found
    return found


def parse_pts_times(stderr: str) -> list[float]:
    """Source timestamps (seconds) reported by the `showinfo` filter, in output order.

    Lets a caller learn which frames a chain actually kept instead of assuming the nominal
    sample times. The two diverge for any resampling filter, and assuming they match is how
    burned storyboard labels ended up half a sampling interval early.
    """
    return [float(m.group(1)) for m in _PTS_TIME_RE.finditer(stderr or "")]


def format_mmss(seconds: float) -> str:
    """Render a timestamp as `MMmSSs` (e.g. 83.4 -> `01m23s`)."""
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}m{seconds % 60:02d}s"


def validate_video_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise MediaOpsError("`path` must be a non-empty local video file path.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise MediaOpsError(f"Video file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise MediaOpsError(f"Unsupported video extension `{path.suffix}`. Supported: {supported}")
    return path


def validate_output_dir(raw_dir: str | None) -> Path:
    if raw_dir is None or not raw_dir.strip():
        # No explicit output_dir: land under the harness-provided scratch root if it set one
        # (env TEMP_DIR — a headless harness may point this at e.g. $WORKSPACE/tmp/.perception), so
        # frames/clips/audio/storyboard/ocr byproducts collect in one predictable place instead
        # of scattering to the system temp. Falls back to system temp for local/standalone use.
        base = _env("TEMP_DIR", "").strip()
        out = Path(base) if base else Path(tempfile.gettempdir()) / "omni-skill-media"
    else:
        out = Path(raw_dir).expanduser()
        if not out.is_absolute():
            out = Path.cwd() / out
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def asset_dir(output_dir: str | None, subdir: str) -> Path:
    root = validate_output_dir(output_dir)
    target = root / subdir
    target.mkdir(parents=True, exist_ok=True)
    return target


def _run(cmd: list[str], timeout: int = 150) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaOpsError(f"`{cmd[0]}` is not installed or not on PATH. Install ffmpeg first.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaOpsError(f"`{cmd[0]}` timed out after {timeout}s.") from exc
    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise MediaOpsError(f"`{cmd[0]}` failed (exit {proc.returncode}):\n" + "\n".join(stderr_tail))
    return proc


def _run_bytes(cmd: list[str], timeout: int = 150) -> bytes:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaOpsError(f"`{cmd[0]}` is not installed or not on PATH. Install ffmpeg first.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaOpsError(f"`{cmd[0]}` timed out after {timeout}s.") from exc
    if proc.returncode != 0:
        stderr_tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-8:]
        raise MediaOpsError(f"`{cmd[0]}` failed (exit {proc.returncode}):\n" + "\n".join(stderr_tail))
    return proc.stdout


@dataclass
class VideoMetadata:
    path: str
    duration_sec: float
    width: int
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    size_mb: float
    sha256: str | None = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2)


def get_video_metadata(raw_path: str) -> VideoMetadata:
    path = validate_video_path(raw_path)
    proc = _run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
    )
    probe = json.loads(proc.stdout or "{}")
    fmt = probe.get("format", {})
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaOpsError(f"No video stream found in {path}")

    fps = 0.0
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        num, _, den = rate.partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    return VideoMetadata(
        path=str(path),
        duration_sec=float(fmt.get("duration", 0.0) or 0.0),
        width=int(video.get("width", 0) or 0),
        height=int(video.get("height", 0) or 0),
        fps=round(fps, 3),
        video_codec=str(video.get("codec_name", "unknown")),
        has_audio=audio is not None,
        audio_codec=str(audio.get("codec_name")) if audio else None,
        size_mb=round(path.stat().st_size / (1024 * 1024), 2),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_segment(start: float, end: float, limit: float | None = None) -> tuple[float, float]:
    start = max(0.0, float(start))
    end = float(end)
    if end <= start:
        raise MediaOpsError("`end` must be greater than `start`.")
    if limit is not None and end - start > limit:
        raise MediaOpsError(f"Segment is {end - start:.1f}s long which exceeds the {limit:.0f}s limit.")
    return round(start, 3), round(end, 3)


def clamp_timestamps(
    timestamps: list[float],
    duration_sec: float,
    max_frames: int,
    hard_cap: int = MAX_KEYFRAMES,
) -> list[float]:
    limit = min(max(1, max_frames), hard_cap)
    cleaned: list[float] = []
    for ts in timestamps:
        ts = max(0.0, float(ts))
        if duration_sec > 0:
            ts = min(ts, max(0.0, duration_sec - 0.05))
        cleaned.append(round(ts, 3))
    seen: set[float] = set()
    deduped: list[float] = []
    for ts in cleaned:
        if ts not in seen:
            seen.add(ts)
            deduped.append(ts)
    return sorted(deduped)[:limit]


def slugify(text: str, max_len: int = 32) -> str:
    """Turn arbitrary text into a safe kebab-case filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") if slug else ""
