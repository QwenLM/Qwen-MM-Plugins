"""MCP tool: MFCC-based dedup over candidate audio clips."""

from __future__ import annotations

import json
import math
import subprocess
from functools import lru_cache
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ._media_utils import MediaOpsError, ffmpeg_path, get_video_metadata, validate_video_path

MAX_DEDUP_AUDIO_CLIPS = 64
_SAMPLE_RATE = 8000
_N_MFCC = 13
_FRAME_LEN = 256  # 32 ms at 8 kHz
_HOP_LEN = 128  # 16 ms
_N_FFT = 512
_N_MELS = 26


class DedupAudioArgs(BaseModel):
    video_path: str = Field()
    segments: list[list[float]] = Field()
    similarity_threshold: float = Field(default=0.95)


TOOL: dict[str, Any] = {"name": "dedup_audio", "args": DedupAudioArgs}


def _extract_pcm(video_path: str, start: float, end: float) -> np.ndarray:
    """Extract a mono audio segment as raw PCM float32 via ffmpeg."""
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        video_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(_SAMPLE_RATE),
        "-f",
        "f32le",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except FileNotFoundError as exc:
        raise MediaOpsError("ffmpeg is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaOpsError("ffmpeg timed out extracting audio.") from exc
    if proc.returncode != 0:
        return np.array([], dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _hz_to_mel(hz: float) -> float:
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


@lru_cache(maxsize=1)
def _mel_filterbank() -> np.ndarray:
    """Triangular mel filterbank over the rFFT bins (params are module constants, so one shape)."""
    n_bins = _N_FFT // 2 + 1
    mel_pts = np.linspace(_hz_to_mel(0.0), _hz_to_mel(_SAMPLE_RATE / 2), _N_MELS + 2)
    bin_pts = np.floor((_N_FFT + 1) * _mel_to_hz(mel_pts) / _SAMPLE_RATE).astype(int)
    fb = np.zeros((_N_MELS, n_bins))
    for i in range(_N_MELS):
        left, mid, right = int(bin_pts[i]), int(bin_pts[i + 1]), int(bin_pts[i + 2])
        if mid > left:
            fb[i, left:mid] = (np.arange(left, mid) - left) / (mid - left)
        if right > mid:
            fb[i, mid:right] = (right - np.arange(mid, right)) / (right - mid)
    return fb


@lru_cache(maxsize=1)
def _dct_matrix() -> np.ndarray:
    """Orthonormal DCT-II matrix (n_mfcc x n_mels); row 0 carries the 1/sqrt(2) scaling."""
    n = np.arange(_N_MELS)
    k = np.arange(_N_MFCC)[:, None]
    m = math.sqrt(2.0 / _N_MELS) * np.cos(math.pi * (n + 0.5) * k / _N_MELS)
    m[0] /= math.sqrt(2.0)
    return m


def _mfcc_fingerprint(pcm: np.ndarray) -> np.ndarray | None:
    """Compute a compact MFCC mean-vector fingerprint.

    Pure numpy (frame -> power spectrum -> mel -> log -> DCT-II): the capability's pyproject
    extra declares numpy only, and fingerprints are never persisted, so matching librosa's
    exact parameterization is unnecessary — only same-impl comparisons matter.
    """
    if len(pcm) < _SAMPLE_RATE * 0.1:
        return None
    n_frames = 1 + (len(pcm) - _FRAME_LEN) // _HOP_LEN
    idx = np.arange(_FRAME_LEN)[None, :] + _HOP_LEN * np.arange(n_frames)[:, None]
    frames = pcm[idx].astype(np.float64) * np.hamming(_FRAME_LEN)
    power = np.abs(np.fft.rfft(frames, _N_FFT)) ** 2
    mel = power @ _mel_filterbank().T
    mfcc = np.log(mel + 1e-10) @ _dct_matrix().T
    return mfcc.mean(axis=0)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom < 1e-9:
        return 0.0
    return float(np.dot(a, b) / denom)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""MFCC-based dedup over candidate audio segments (no LLM). Compares audio fingerprints so only
    distinct clips need further attention.

    Args:
        video_path: Path to the source video file.
        segments: List of [start, end] pairs (seconds) for candidate audio clips.
        similarity_threshold: Cosine similarity above which two clips are considered duplicates (0.0–1.0).
    """
    raw_path = arguments["video_path"]
    segments = arguments["segments"]
    threshold = arguments.get("similarity_threshold", 0.95)

    path = validate_video_path(raw_path)
    meta = get_video_metadata(str(path))
    if not meta.has_audio:
        raise MediaOpsError("The video has no audio stream.")

    threshold = min(max(float(threshold), 0.0), 1.0)

    if len(segments) > MAX_DEDUP_AUDIO_CLIPS:
        segments = segments[:MAX_DEDUP_AUDIO_CLIPS]

    reps: list[tuple[list[float], np.ndarray]] = []
    unique: list[dict] = []
    duplicates: list[dict] = []

    for seg in segments:
        if not isinstance(seg, (list, tuple)) or len(seg) != 2:
            continue
        start, end = float(seg[0]), float(seg[1])
        start = max(0.0, start)
        end = min(end, meta.duration_sec)
        if end <= start:
            continue

        pcm = _extract_pcm(str(path), start, end)
        fp = _mfcc_fingerprint(pcm)
        if fp is None:
            continue

        match = None
        for rep_seg, rep_fp in reps:
            if _cosine_similarity(fp, rep_fp) >= threshold:
                match = rep_seg
                break

        if match is None:
            reps.append(([start, end], fp))
            unique.append({"segment": [start, end]})
        else:
            duplicates.append({"segment": [start, end], "duplicate_of": match})

    result = {
        "similarity_threshold": threshold,
        "candidate_count": len(segments),
        "unique": unique,
        "duplicates": duplicates,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
