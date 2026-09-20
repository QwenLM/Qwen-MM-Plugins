"""Reference launcher for the dubbing service used by Omni ChatCut video translation.

The server loads IndexTTS2 once and keeps it resident on one GPU. It also exposes
Demucs source separation and TEN-VAD speech detection. Keep these heavyweight GPU
dependencies outside the MCP process and point the plugin at this HTTP endpoint.

Prerequisites (this script does not install model dependencies):
  1. Install IndexTTS2 and make the ``indextts`` package importable.
  2. Download IndexTTS2 checkpoints and set ``INDEXTTS_CHECKPOINTS``.
  3. Install FastAPI, Uvicorn, NumPy, SoundFile, Demucs, TEN-VAD, and ffmpeg.

Launch from an environment containing those dependencies:
    HF_ENDPOINT=https://hf-mirror.com \\
    python launch_dubbing_server.py --host 0.0.0.0 --port 23333

Wire it to Omni ChatCut on the machine running the MCP server / agent:
    export QWEN_MM_DUBBING_SERVER_URL=http://<server-host>:23333
    curl http://<server-host>:23333/health

Run exactly one worker per process. IndexTTS2 is not thread-safe, so inference is
serialized by an in-process lock. For multiple GPUs, run one process per GPU behind
a load balancer.

Serves:
    GET  /health
    POST /tts and /tts_upload
    POST /separate and /separate_upload
    POST /vad and /vad_upload
    GET  /audio/{filename}

Environment variables (all optional except having valid checkpoints):
    INDEXTTS_CHECKPOINTS   model directory (default: checkpoints)
    INDEXTTS_DEFAULT_VOICE default reference voice WAV (default: empty)
    INDEXTTS_OUTPUT_DIR    generated audio directory (default: output)
    INDEXTTS_SEPARATE_DIR  source-separation output directory (default: output/separated)
    INDEXTTS_FP16          enable fp16 (default: 1)
    INDEXTTS_EXIT_ON_CUDA_ERROR  exit on an unrecoverable CUDA error
                                 (default: 0; otherwise mark the service unavailable)
    INDEXTTS_HOST / INDEXTTS_PORT
"""

import argparse
import base64
import os
import subprocess
import sys
import threading
import time
import uuid
import wave
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

CHECKPOINTS_DIR = os.environ.get("INDEXTTS_CHECKPOINTS", "checkpoints")
DEFAULT_VOICE = os.environ.get("INDEXTTS_DEFAULT_VOICE", "")
OUTPUT_DIR = os.environ.get("INDEXTTS_OUTPUT_DIR", "output")
CACHE_DIR = os.environ.get("INDEXTTS_CACHE_DIR", "cache")
SEPARATE_DIR = os.environ.get("INDEXTTS_SEPARATE_DIR", os.path.join(OUTPUT_DIR, "separated"))
USE_FP16 = os.environ.get("INDEXTTS_FP16", "1") not in ("0", "false", "False", "")
EXIT_ON_CUDA_ERROR = os.environ.get("INDEXTTS_EXIT_ON_CUDA_ERROR", "0") not in ("0", "false", "False", "")

# CUDA contexts cannot recover from device-side assertions; reject later requests.
STATE = {"tts": None, "device": None, "cuda_broken": None}

# IndexTTS2 has mutable shared state and must serialize inference.
INFER_LOCK = threading.Lock()


def _is_fatal_cuda_error(exc: BaseException) -> bool:
    """Return whether an exception indicates an unrecoverable CUDA context."""
    msg = f"{exc!r}"
    return (
        "device-side assert" in msg or "CUDA error" in msg or "CUDA_ERROR" in msg or "an illegal memory access" in msg
    )


def _mark_cuda_broken(exc: BaseException) -> None:
    """Record an unrecoverable CUDA failure and optionally exit for supervisor restart."""
    STATE["cuda_broken"] = f"{exc!r}"
    print(f"Unrecoverable CUDA error; service marked unavailable: {exc!r}", flush=True)
    if EXIT_ON_CUDA_ERROR:
        print("INDEXTTS_EXIT_ON_CUDA_ERROR=1; exiting for supervisor restart", flush=True)
        os._exit(70)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model once during service startup."""
    from indextts.infer_v2 import IndexTTS2

    cfg_path = os.path.join(CHECKPOINTS_DIR, "config.yaml")
    if not os.path.exists(cfg_path):
        raise RuntimeError(f"Model configuration not found: {cfg_path}; download checkpoints to {CHECKPOINTS_DIR}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"Loading IndexTTS2 (fp16={USE_FP16}) ...")
    tts = IndexTTS2(
        cfg_path=cfg_path,
        model_dir=CHECKPOINTS_DIR,
        use_fp16=USE_FP16,
        use_cuda_kernel=False,
    )
    STATE["tts"] = tts
    STATE["device"] = tts.device
    print(f"IndexTTS2 ready; device={tts.device}")
    yield
    STATE["tts"] = None


app = FastAPI(title="IndexTTS2 Server", version="1.0.0", lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize")
    voice: Optional[str] = Field(
        None, description="Server-local reference voice WAV; omit to use the configured default"
    )
    voice_base64: Optional[str] = Field(
        None,
        description="Base64 reference audio; takes precedence over voice and is cached locally",
    )
    voice_filename: Optional[str] = Field(
        None, description="Filename used to preserve the voice_base64 extension; defaults to .wav"
    )
    output_name: Optional[str] = Field(None, description="Output filename without a directory; generated when omitted")
    emo_alpha: float = Field(1.0, description="Emotion strength in [0, 1]")
    use_emo_text: bool = Field(False, description="Infer emotion from text")
    emo_text: Optional[str] = Field(None, description="Text used for emotion inference; defaults to text")
    emo_vector: Optional[List[float]] = Field(None, description="Eight-dimensional emotion vector")
    max_text_tokens_per_segment: int = Field(120, description="Maximum text tokens per segment")
    trim_silence: bool = Field(True, description="Trim leading and trailing silence from output audio")
    trim_top_db: float = Field(30.0, description="Silence threshold in dB; lower values trim more aggressively")
    trim_ref_silence: bool = Field(True, description="Trim leading and trailing silence from reference audio")
    target_duration: Optional[float] = Field(
        None,
        description="Target duration in seconds; applies tempo adjustment after trimming",
    )
    return_audio: bool = Field(True, description="Return base64 audio in the response")


class TTSResponse(BaseModel):
    output_path: Optional[str] = None
    filename: Optional[str] = None
    duration_sec: float
    infer_time_sec: float
    sample_rate: int
    stretch_rate: Optional[float] = None
    cached_voice: Optional[str] = None
    audio_base64: Optional[str] = None


class SeparateRequest(BaseModel):
    audio: Optional[str] = Field(None, description="Server-local audio path to separate")
    audio_base64: Optional[str] = Field(
        None,
        description="Base64 audio to separate; takes precedence over audio and is cached locally",
    )
    audio_filename: Optional[str] = Field(
        None, description="Filename used to preserve the audio_base64 extension; defaults to .wav"
    )
    model: str = Field("htdemucs", description="Demucs model name")
    two_stems: Optional[str] = Field(
        "vocals",
        description="Optional two-stem target such as vocals; omit for full separation",
    )
    mp3: bool = Field(True, description="Return MP3 stems instead of WAV")
    return_audio: bool = Field(True, description="Return base64 stems in the response")


class SeparateResponse(BaseModel):
    output_dir: str
    stems: Dict[str, str]
    infer_time_sec: float
    stems_base64: Optional[Dict[str, str]] = None


class VadRequest(BaseModel):
    audio: Optional[str] = Field(None, description="Server-local audio path to analyze")
    audio_base64: Optional[str] = Field(
        None,
        description="Base64 audio to analyze; takes precedence over audio and is cached locally",
    )
    audio_filename: Optional[str] = Field(
        None, description="Filename used to preserve the audio_base64 extension; defaults to .wav"
    )
    separate_first: bool = Field(True, description="Run Demucs vocal separation before VAD")
    model: str = Field("htdemucs", description="Demucs model used when separate_first is true")
    threshold: float = Field(0.5, ge=0.0, le=1.0, description="Speech threshold in [0, 1]")
    hop_size: int = Field(
        256,
        ge=80,
        le=1024,
        description="Frame size in samples; 256=16 ms and 160=10 ms",
    )
    min_speech: float = Field(0.2, ge=0.0, le=10.0, description="Minimum speech duration in seconds")
    min_silence: float = Field(0.3, ge=0.0, le=10.0, description="Minimum silence gap in seconds")
    pad: float = Field(0.1, ge=0.0, le=2.0, description="Padding added around each segment in seconds")


class VadResponse(BaseModel):
    file: str
    separated: bool
    vocals_path: Optional[str] = None
    sample_rate: int
    duration: float
    num_segments: int
    segments: List[Dict[str, float]]
    infer_time_sec: float


def _apply_vad_trim(y, top_db: float):
    """Trim leading and trailing silence using an energy threshold."""
    if y.size == 0:
        return y
    ref = np.max(np.abs(y))
    if ref == 0:
        return y
    threshold = ref * 10 ** (-top_db / 20)
    above = np.where(np.abs(y) >= threshold)[0]
    if above.size == 0:
        return y
    return y[above[0] : above[-1] + 1]


def _trim_silence(path: str, top_db: float = 30.0):
    """Trim leading and trailing silence in place."""
    y, sr = sf.read(path)
    sf.write(path, _apply_vad_trim(y, top_db), sr)


def _atempo_chain(factor: float) -> List[float]:
    """Split a tempo factor into ffmpeg-compatible factors in [0.5, 2.0]."""
    filters: List[float] = []
    remaining = factor
    while remaining > 2.0:
        filters.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        filters.append(0.5)
        remaining /= 0.5
    filters.append(remaining)
    return filters


def _fit_duration(path: str, target_sec: float) -> float:
    """Fit audio to target_sec in place with ffmpeg atempo and return the applied factor."""
    y, sr = sf.read(path)
    cur_sec = len(y) / sr if sr else 0.0
    if cur_sec <= 0 or target_sec <= 0:
        return 1.0
    factor = cur_sec / target_sec
    chain = ",".join(f"atempo={f:.6f}" for f in _atempo_chain(factor))

    # ffmpeg cannot read and write the same file.
    tmp_path = path + ".stretch.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-filter:a", chain, tmp_path],
        check=True,
        capture_output=True,
    )

    # Correct millisecond-level atempo drift to the exact sample count.
    ys, srs = sf.read(tmp_path)
    os.remove(tmp_path)
    target_n = int(round(target_sec * srs))
    if len(ys) > target_n:
        ys = ys[:target_n]
    elif len(ys) < target_n:
        ys = np.pad(ys, (0, target_n - len(ys)))
    sf.write(path, ys, srs)
    return round(factor, 4)


def _wav_info(path: str):
    """Read WAV duration and sample rate."""
    with wave.open(path, "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        return frames / float(rate) if rate else 0.0, rate


def _save_upload_audio(
    data: bytes, suffix: str = ".wav", prefix: str = "ref", trim: bool = False, top_db: float = 30.0
) -> str:
    """Save uploaded audio to CACHE_DIR and optionally trim it to WAV."""
    if not data:
        raise HTTPException(status_code=400, detail="Audio payload is empty")
    os.makedirs(CACHE_DIR, exist_ok=True)
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    ts = time.strftime("%Y%m%d_%H%M%S")
    stem = f"{prefix}_{ts}_{uuid.uuid4().hex[:8]}"
    raw_path = os.path.join(CACHE_DIR, stem + suffix)
    with open(raw_path, "wb") as f:
        f.write(data)
    if not trim:
        return raw_path
    y, sr = sf.read(raw_path)
    final_path = os.path.join(CACHE_DIR, stem + ".wav")
    sf.write(final_path, _apply_vad_trim(y, top_db), sr)
    if final_path != raw_path:
        os.remove(raw_path)
    return final_path


def _trim_ref_to_cache(src_path: str, top_db: float = 30.0) -> str:
    """Trim a server-local reference into CACHE_DIR without modifying the source."""
    y, sr = sf.read(src_path)
    os.makedirs(CACHE_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(CACHE_DIR, f"ref_{ts}_{uuid.uuid4().hex[:8]}_trim.wav")
    sf.write(path, _apply_vad_trim(y, top_db), sr)
    return path


def _resolve_voice(req: "TTSRequest"):
    """Resolve the reference voice, preferring voice_base64 over voice and the configured default."""
    if req.voice_base64:
        try:
            data = base64.b64decode(req.voice_base64)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to decode voice_base64: {e!r}")
        suffix = os.path.splitext(req.voice_filename or "")[1] or ".wav"
        return _save_upload_audio(data, suffix, prefix="ref", trim=req.trim_ref_silence, top_db=req.trim_top_db), True

    if req.voice:
        if not os.path.exists(req.voice):
            raise HTTPException(status_code=400, detail=f"Reference voice does not exist: {req.voice}")
        src = req.voice
    else:
        if not DEFAULT_VOICE:
            raise HTTPException(
                status_code=400,
                detail="voice or voice_base64 is required when INDEXTTS_DEFAULT_VOICE is unset",
            )
        if not os.path.exists(DEFAULT_VOICE):
            raise HTTPException(status_code=400, detail=f"Default reference voice does not exist: {DEFAULT_VOICE}")
        src = DEFAULT_VOICE

    # Preserve server-local inputs and trim into a cached copy.
    if req.trim_ref_silence:
        return _trim_ref_to_cache(src, req.trim_top_db), True
    return src, False


def _validate_generation_params(req: "TTSRequest", tts_model) -> int:
    """Validate generation parameters that affect model index bounds."""
    # Reserve positions for the start and stop tokens.
    max_allowed = int(getattr(tts_model.gpt, "max_text_tokens", 600))
    segment_len = req.max_text_tokens_per_segment
    if segment_len < 2:
        raise HTTPException(status_code=400, detail="max_text_tokens_per_segment must be at least 2")
    if segment_len > max_allowed:
        print(f">> max_text_tokens_per_segment={segment_len} exceeds the model limit; clamped to {max_allowed}")
        segment_len = max_allowed

    if req.emo_vector is not None:
        if len(req.emo_vector) != 8:
            raise HTTPException(
                status_code=400,
                detail=f"emo_vector must contain 8 values; received {len(req.emo_vector)} values",
            )
        req.emo_vector = [max(0.0, min(1.0, float(v))) for v in req.emo_vector]

    if not 0.0 <= req.emo_alpha <= 1.0:
        raise HTTPException(status_code=400, detail="emo_alpha must be in [0, 1]")

    return segment_len


def _synthesize(req: "TTSRequest") -> "TTSResponse":
    """Shared synthesis implementation for /tts and /tts_upload."""
    if STATE["cuda_broken"]:
        raise HTTPException(
            status_code=503,
            detail=f"CUDA context is unusable; restart the service: {STATE['cuda_broken']}",
        )

    tts_model = STATE["tts"]
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model is not ready")

    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    segment_len = _validate_generation_params(req, tts_model)

    voice, cached = _resolve_voice(req)

    # Remove temporary server output after embedding it in the response.
    is_temp = req.output_name is None and req.return_audio
    filename = req.output_name or f"tts_{uuid.uuid4().hex[:12]}.wav"
    if os.path.basename(filename) != filename:
        raise HTTPException(status_code=400, detail="output_name must not contain a directory")
    output_path = os.path.join(OUTPUT_DIR, filename)

    start = time.perf_counter()
    # Serialize inference because the model is not thread-safe.
    with INFER_LOCK:
        if STATE["cuda_broken"]:
            raise HTTPException(
                status_code=503,
                detail=f"CUDA context is unusable; restart the service: {STATE['cuda_broken']}",
            )
        try:
            tts_model.infer(
                spk_audio_prompt=voice,
                text=req.text,
                output_path=output_path,
                emo_alpha=req.emo_alpha,
                emo_vector=req.emo_vector,
                use_emo_text=req.use_emo_text,
                emo_text=req.emo_text,
                max_text_tokens_per_segment=segment_len,
            )
        except Exception as e:  # noqa: BLE001
            if _is_fatal_cuda_error(e):
                _mark_cuda_broken(e)
                raise HTTPException(
                    status_code=503,
                    detail=f"Synthesis failed and the CUDA context is unusable; restart the service: {e!r}",
                )
            raise HTTPException(status_code=500, detail=f"Synthesis failed: {e!r}")

    infer_time = time.perf_counter() - start
    if not os.path.exists(output_path):
        raise HTTPException(status_code=500, detail="Synthesis completed without producing an audio file")

    if req.trim_silence:
        try:
            _trim_silence(output_path, top_db=req.trim_top_db)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Silence trimming failed: {e!r}")

    stretch_rate = None
    if req.target_duration is not None:
        if req.target_duration <= 0:
            raise HTTPException(status_code=400, detail="target_duration must be greater than zero")
        try:
            stretch_rate = _fit_duration(output_path, req.target_duration)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Duration fitting failed: {e!r}")

    duration, sample_rate = _wav_info(output_path)
    audio_b64 = None
    if req.return_audio:
        with open(output_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("ascii")

    # Do not expose paths to temporary server output.
    persisted_path = os.path.abspath(output_path)
    if is_temp:
        try:
            os.remove(output_path)
        except OSError:
            pass
        persisted_path = None

    return TTSResponse(
        output_path=persisted_path,
        filename=None if is_temp else filename,
        duration_sec=round(duration, 3),
        infer_time_sec=round(infer_time, 3),
        sample_rate=sample_rate,
        stretch_rate=stretch_rate,
        cached_voice=os.path.abspath(voice) if cached else None,
        audio_base64=audio_b64,
    )


def _run_demucs(
    input_path: str, out_dir: str, model: str = "htdemucs", two_stems: Optional[str] = "vocals", mp3: bool = True
):
    """Run Demucs and return the stem directory and stem paths.

    Equivalent command:
        python -m demucs [--two-stems=vocals] [--mp3] -n <model> -o <out> <input>
    """
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, "-m", "demucs", "-n", model, "-o", out_dir]
    if two_stems:
        cmd.append(f"--two-stems={two_stems}")
    if mp3:
        cmd.append("--mp3")
    cmd.append(input_path)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Python executable not found; cannot run Demucs")
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or str(e)).strip()
        if "No module named demucs" in detail:
            detail = "Demucs is not installed; run: uv pip install demucs"
        raise HTTPException(status_code=500, detail=f"Demucs separation failed: {detail[-500:]}")

    # Demucs uses the input stem as its output subdirectory.
    track = os.path.splitext(os.path.basename(input_path))[0]
    stem_dir = os.path.join(out_dir, model, track)
    if not os.path.isdir(stem_dir):
        raise HTTPException(status_code=500, detail=f"Demucs output directory not found: {stem_dir}")
    stems = {os.path.splitext(f)[0]: os.path.join(stem_dir, f) for f in sorted(os.listdir(stem_dir))}
    if not stems:
        raise HTTPException(status_code=500, detail="Demucs produced no stems")
    return stem_dir, stems


def _resolve_audio_input(audio: Optional[str], audio_base64: Optional[str], audio_filename: Optional[str]) -> str:
    """Resolve input audio, preferring audio_base64 over a server-local path."""
    if audio_base64:
        try:
            data = base64.b64decode(audio_base64)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to decode audio_base64: {e!r}")
        suffix = os.path.splitext(audio_filename or "")[1] or ".wav"
        return _save_upload_audio(data, suffix, prefix="inp")
    if audio:
        if not os.path.exists(audio):
            raise HTTPException(status_code=400, detail=f"Input audio does not exist: {audio}")
        return audio
    raise HTTPException(status_code=400, detail="audio or audio_base64 is required")


def _separate(req: "SeparateRequest") -> "SeparateResponse":
    """Shared separation implementation for /separate and /separate_upload."""
    input_path = _resolve_audio_input(req.audio, req.audio_base64, req.audio_filename)
    start = time.perf_counter()
    stem_dir, stems = _run_demucs(input_path, SEPARATE_DIR, req.model, req.two_stems, req.mp3)
    infer_time = time.perf_counter() - start

    stems_b64 = None
    if req.return_audio:
        stems_b64 = {}
        for name, p in stems.items():
            with open(p, "rb") as f:
                stems_b64[name] = base64.b64encode(f.read()).decode("ascii")

    return SeparateResponse(
        output_dir=os.path.abspath(stem_dir),
        stems={k: os.path.abspath(v) for k, v in stems.items()},
        infer_time_sec=round(infer_time, 3),
        stems_base64=stems_b64,
    )


VAD_SAMPLE_RATE = 16000  # TEN-VAD requires 16 kHz audio.


def _decode_audio_16k(path: str) -> np.ndarray:
    """Decode audio to a 16 kHz mono int16 NumPy array."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        path,
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(VAD_SAMPLE_RATE),
        "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "ignore").strip()
        raise HTTPException(status_code=500, detail=f"ffmpeg decoding failed: {detail[-500:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16)


def _run_ten_vad(audio: np.ndarray, hop_size: int, threshold: float) -> np.ndarray:
    """Run TEN-VAD frame by frame and return binary speech flags."""
    try:
        from ten_vad import TenVad
    except ImportError:
        raise HTTPException(status_code=500, detail="ten_vad is not installed; run: uv pip install ten-vad")
    try:
        vad = TenVad(hop_size, threshold)
    except OSError as e:  # Missing a native dependency such as libc++.so.1.
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load the TEN-VAD native library: {e!r}; install the required system library, for example: apt install libc++1",
        )
    num_frames = (len(audio) + hop_size - 1) // hop_size
    flags = np.zeros(num_frames, dtype=np.int8)
    for i in range(num_frames):
        frame = audio[i * hop_size : (i + 1) * hop_size]
        if len(frame) < hop_size:
            frame = np.pad(frame, (0, hop_size - len(frame)))
        _, flag = vad.process(frame)
        flags[i] = flag
    return flags


def _frames_to_segments(flags, hop_size, sample_rate, min_speech, min_silence, pad, total_duration=None):
    """Convert frame flags into smoothed speech intervals in seconds."""
    frame_dur = hop_size / sample_rate
    total_dur = len(flags) * frame_dur if total_duration is None else total_duration

    # Collect contiguous speech frames.
    segments = []
    start = None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            segments.append([start, i])
            start = None
    if start is not None:
        segments.append([start, len(flags)])

    if not segments:
        return []

    # Merge intervals separated by short gaps.
    min_silence_frames = min_silence / frame_dur
    merged = [segments[0]]
    for seg in segments[1:]:
        if seg[0] - merged[-1][1] <= min_silence_frames:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    # Drop short speech and add padding.
    min_speech_frames = min_speech / frame_dur
    result = []
    for s, e in merged:
        if e - s < min_speech_frames:
            continue
        start_t = max(0.0, s * frame_dur - pad)
        end_t = min(total_dur, e * frame_dur + pad)
        result.append((start_t, end_t))

    # Merge intervals that overlap after padding.
    final = []
    for seg in result:
        if final and seg[0] <= final[-1][1]:
            final[-1] = (final[-1][0], max(final[-1][1], seg[1]))
        else:
            final.append(seg)

    return [{"start_time": round(s, 3), "end_time": round(e, 3)} for s, e in final]


def _vad(req: "VadRequest") -> "VadResponse":
    """Shared VAD implementation for /vad and /vad_upload."""
    src = _resolve_audio_input(req.audio, req.audio_base64, req.audio_filename)

    separated = False
    vocals_path = None
    analyze_path = src
    if req.separate_first:
        # Use lossless WAV stems for VAD.
        _, stems = _run_demucs(src, SEPARATE_DIR, req.model, "vocals", mp3=False)
        if "vocals" not in stems:
            raise HTTPException(status_code=500, detail=f"Demucs response has no vocals stem: {list(stems)}")
        vocals_path = stems["vocals"]
        analyze_path = vocals_path
        separated = True

    start = time.perf_counter()
    audio = _decode_audio_16k(analyze_path)
    flags = _run_ten_vad(audio, req.hop_size, req.threshold)
    segments = _frames_to_segments(
        flags,
        req.hop_size,
        VAD_SAMPLE_RATE,
        req.min_speech,
        req.min_silence,
        req.pad,
        total_duration=len(audio) / VAD_SAMPLE_RATE,
    )
    infer_time = time.perf_counter() - start

    return VadResponse(
        file=os.path.abspath(analyze_path),
        separated=separated,
        vocals_path=os.path.abspath(vocals_path) if vocals_path else None,
        sample_rate=VAD_SAMPLE_RATE,
        duration=round(len(audio) / VAD_SAMPLE_RATE, 3),
        num_segments=len(segments),
        segments=segments,
        infer_time_sec=round(infer_time, 3),
    )


@app.get("/health")
def health():
    ready = STATE["tts"] is not None and not STATE["cuda_broken"]
    if STATE["cuda_broken"]:
        status = "error"
    elif STATE["tts"] is None:
        status = "loading"
    else:
        status = "ok"
    return {
        "status": status,
        "model_loaded": ready,
        "device": STATE["device"],
        "default_voice": DEFAULT_VOICE,
        "checkpoints": CHECKPOINTS_DIR,
        "cuda_broken": STATE["cuda_broken"],
    }


@app.post("/tts", response_model=TTSResponse)
def tts(req: TTSRequest):
    """Synthesize speech from a server-local or base64 reference voice."""
    return _synthesize(req)


@app.post("/tts_upload", response_model=TTSResponse)
async def tts_upload(
    file: UploadFile = File(..., description="Reference audio file"),
    text: str = Form(...),
    output_name: Optional[str] = Form(None),
    emo_alpha: float = Form(1.0),
    use_emo_text: bool = Form(False),
    emo_text: Optional[str] = Form(None),
    max_text_tokens_per_segment: int = Form(120),
    trim_silence: bool = Form(True),
    trim_top_db: float = Form(30.0),
    trim_ref_silence: bool = Form(True),
    target_duration: Optional[float] = Form(None),
    return_audio: bool = Form(True),
):
    """Synthesize speech from a multipart reference audio upload."""
    data = await file.read()
    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    # Trim the upload once before synthesis.
    voice_path = _save_upload_audio(data, suffix, prefix="ref", trim=trim_ref_silence, top_db=trim_top_db)
    req = TTSRequest(
        text=text,
        voice=voice_path,
        output_name=output_name,
        emo_alpha=emo_alpha,
        use_emo_text=use_emo_text,
        emo_text=emo_text,
        max_text_tokens_per_segment=max_text_tokens_per_segment,
        trim_silence=trim_silence,
        trim_top_db=trim_top_db,
        trim_ref_silence=False,
        target_duration=target_duration,
        return_audio=return_audio,
    )
    resp = await run_in_threadpool(_synthesize, req)
    resp.cached_voice = os.path.abspath(voice_path)
    return resp


@app.post("/separate", response_model=SeparateResponse)
def separate(req: SeparateRequest):
    """Separate audio with Demucs using a server-local or base64 input."""
    return _separate(req)


@app.post("/separate_upload", response_model=SeparateResponse)
async def separate_upload(
    file: UploadFile = File(..., description="Audio file to separate"),
    model: str = Form("htdemucs"),
    two_stems: Optional[str] = Form("vocals"),
    mp3: bool = Form(True),
    return_audio: bool = Form(True),
):
    """Separate a multipart audio upload."""
    data = await file.read()
    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    input_path = _save_upload_audio(data, suffix, prefix="sep")
    req = SeparateRequest(
        audio=input_path,
        model=model,
        two_stems=two_stems or None,
        mp3=mp3,
        return_audio=return_audio,
    )
    return _separate(req)


@app.post("/vad", response_model=VadResponse)
def vad(req: VadRequest):
    """Detect speech intervals with TEN-VAD."""
    return _vad(req)


@app.post("/vad_upload", response_model=VadResponse)
async def vad_upload(
    file: UploadFile = File(..., description="Audio file to analyze"),
    separate_first: bool = Form(True),
    model: str = Form("htdemucs"),
    threshold: float = Form(0.5),
    hop_size: int = Form(256),
    min_speech: float = Form(0.2),
    min_silence: float = Form(0.3),
    pad: float = Form(0.1),
):
    """Run VAD on a multipart audio upload."""
    data = await file.read()
    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    input_path = _save_upload_audio(data, suffix, prefix="vad")
    req = VadRequest(
        audio=input_path,
        separate_first=separate_first,
        model=model,
        threshold=threshold,
        hop_size=hop_size,
        min_speech=min_speech,
        min_silence=min_silence,
        pad=pad,
    )
    return _vad(req)


@app.get("/audio/{filename}")
def download_audio(filename: str):
    """Download persisted generated audio."""
    if os.path.basename(filename) != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="audio/wav", filename=filename)


def main():
    parser = argparse.ArgumentParser(description="IndexTTS2 HTTP inference service")
    parser.add_argument("--host", default=os.environ.get("INDEXTTS_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("INDEXTTS_PORT", "23333")))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
