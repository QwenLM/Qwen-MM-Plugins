"""Build Hierarchical Graph Memory from a video.

Pipeline: Segmentation -> Subgraph Extraction -> Hierarchical Aggregation -> Embeddings.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import oss2
except ImportError:
    oss2 = None

from embeddings import EmbeddingIndex
from env_config import get_env
from llm_client import DEFAULT_MODEL, call_vlm_curl, extract_json
from prompts import (
    HIERARCHICAL_AGGREGATION_PROMPT,
    HIERARCHICAL_AGGREGATION_WINDOW_PROMPT,
    ROOT_SYNTHESIS_PROMPT,
    SUBGRAPH_CONSTRUCTION_PROMPT,
)
from schema import (
    Edge,
    Entity,
    HierarchicalGraphMemory,
    MacroEvent,
    MacroRelation,
    MicroEvent,
    OnScreenText,
    Subgraph,
    SuperEvent,
    SuperRelation,
    VideoRoot,
)
from time_utils import sec_to_time_str, time_str_to_sec

# ── OSS config ──
OSS_AK = get_env("OSS_AK", "")
OSS_SK = get_env("OSS_SK", "")
OSS_ENDPOINT = get_env("OSS_ENDPOINT", "")
DST_BUCKET_NAME = get_env("OSS_BUCKET") or get_env("OSS_BUCKET_NAME", "")
DST_VIDEO_PREFIX = get_env("OSS_VIDEO_CLIP_PREFIX", "tmp/video_clips")
URL_EXPIRY = int(get_env("OSS_URL_EXPIRY", "7200"))
_USE_OSS = bool(OSS_AK and OSS_SK and oss2)

FRAME_WORKERS = 10
_ffmpeg_sem = threading.Semaphore(FRAME_WORKERS)

# Fallback threshold when auto-select yields none. 27.0 is the value PySceneDetect's ContentDetector
# uses as its default; this module does NOT depend on PySceneDetect — segmentation is a custom HLS
# frame-diff detector (see step1_scene_detect_segmentation).
DEFAULT_SCENE_THRESHOLD = 27.0


def _ffmpeg_seek_jpeg(video_path: str, ts: float, scale: str, q: int = 5, timeout: int = 120) -> bytes | None:
    """Grab one JPEG frame at `ts` via ffmpeg seek (throttled by _ffmpeg_sem).
    Returns raw JPEG bytes, or None on failure/timeout."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-ss",
        str(ts),
        "-i",
        video_path,
        "-an",
        "-frames:v",
        "1",
        "-vf",
        f"scale={scale}",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        str(q),
        "pipe:1",
    ]
    _ffmpeg_sem.acquire()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    finally:
        _ffmpeg_sem.release()
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def extract_frames_base64(
    video_path: str,
    start_sec: float,
    end_sec: float,
    fps: float = 0.5,
    max_dim: int = 1024,
    max_frames: int = 250,
) -> list[tuple[str, str]]:
    """Extract frames via parallel ffmpeg seek, return [(base64_data_url, timestamp_label)]."""
    duration = end_sec - start_sec
    if duration <= 0:
        return []
    num_frames = max(4, min(max_frames, int(duration * fps) + 1))
    timestamps = [start_sec + duration * i / max(1, num_frames - 1) for i in range(num_frames)]

    def _extract_one(ts: float):
        data = _ffmpeg_seek_jpeg(video_path, ts, f"{max_dim}:-2", q=5, timeout=120)
        if data is None:
            return None
        b64 = base64.b64encode(data).decode()
        return (f"data:image/jpeg;base64,{b64}", sec_to_time_str(ts), ts)

    with ThreadPoolExecutor(max_workers=FRAME_WORKERS) as pool:
        results = list(pool.map(_extract_one, timestamps))

    return [(r[0], r[1]) for r in sorted((r for r in results if r), key=lambda x: x[2])]


def get_video_duration(video_path: str, retries: int = 3) -> float:
    cmds = [
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
    ]
    last_err = None
    for cmd in cmds:
        for attempt in range(retries):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except Exception as e:
                last_err = str(e)
                break
            stdout = result.stdout.strip()
            if stdout and stdout != "N/A":
                try:
                    return float(stdout)
                except ValueError:
                    last_err = f"cannot parse duration '{stdout}'"
                    break
            last_err = result.stderr.strip() or f"empty output (rc={result.returncode})"
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise RuntimeError(f"ffprobe could not determine duration after all strategies for {video_path}: {last_err}")


def clip_and_upload_video(
    video_path: str,
    start_sec: float,
    end_sec: float,
    max_dim: int = 1024,
    crf: int = 28,
    _max_retries: int = 2,
) -> str:
    """Clip a video segment with ffmpeg, upload to OSS, return signed HTTPS URL."""
    duration = end_sec - start_sec
    min_size = max(50_000, int(duration * 500))

    for attempt in range(_max_retries):
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_sec),
            "-t",
            str(duration),
            "-i",
            video_path,
            "-vf",
            f"scale='min({max_dim},iw)':'min({max_dim},ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            str(crf),
            "-c:a",
            "aac",
            "-threads",
            "4",
            "-movflags",
            "+faststart",
            tmp.name,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                print(f"  ffmpeg clip failed (rc={result.returncode}): {result.stderr[-300:]}")
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
                if attempt < _max_retries - 1:
                    time.sleep(5)
                    continue
                return ""
        except Exception as e:
            print(f"  ffmpeg clip error: {e}")
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
            if attempt < _max_retries - 1:
                time.sleep(5)
                continue
            return ""

        file_size = os.path.getsize(tmp.name) if os.path.exists(tmp.name) else 0
        if file_size < min_size:
            print(
                f"  ffmpeg output too small: {file_size} bytes (min {min_size} for {duration:.0f}s clip), attempt {attempt + 1}/{_max_retries}"
            )
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
            if attempt < _max_retries - 1:
                time.sleep(5)
                continue
            return ""

        h = hashlib.md5()
        with open(tmp.name, "rb") as fh:
            for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        md5 = h.hexdigest()
        key = f"{DST_VIDEO_PREFIX}/{md5}.mp4"

        size_mb = os.path.getsize(tmp.name) / 1024 / 1024
        auth = oss2.Auth(OSS_AK, OSS_SK)
        bucket = oss2.Bucket(auth, OSS_ENDPOINT, DST_BUCKET_NAME)
        with open(tmp.name, "rb") as fh:
            bucket.put_object(key, fh)
        url = bucket.sign_url("GET", key, URL_EXPIRY).replace("http://", "https://")
        os.unlink(tmp.name)
        print(f"  Video clip uploaded: {size_mb:.1f}MB")
        return url

    return ""


def _find_optimal_threshold(
    cut_times: list[float],
    cut_scores: list[float],
    start_sec: float,
    end_sec: float,
    min_scene_sec: float,
    max_scene_sec: float,
) -> float:
    """Binary search for threshold that produces segments with median duration near (min+max)/2."""
    target_median = (min_scene_sec + max_scene_sec) / 2
    unique_scores = sorted(set(cut_scores))
    if not unique_scores:
        return DEFAULT_SCENE_THRESHOLD

    def _simulate(thresh: float) -> float:
        boundaries = [t for t, s in zip(cut_times, cut_scores) if s >= thresh]
        merged: list[float] = []
        last = start_sec
        for b in boundaries:
            if b - last >= min_scene_sec:
                merged.append(b)
                last = b
        prev = start_sec
        durations = []
        for b in merged:
            durations.append(b - prev)
            prev = b
        durations.append(end_sec - prev)
        if not durations:
            return float("inf")
        durations.sort()
        return durations[len(durations) // 2]

    lo, hi = 0, len(unique_scores) - 1
    best_thresh = unique_scores[0]
    best_diff = float("inf")

    while lo <= hi:
        mid = (lo + hi) // 2
        thresh = unique_scores[mid]
        median = _simulate(thresh)
        diff = abs(median - target_median)
        if diff < best_diff:
            best_diff = diff
            best_thresh = thresh
        if median < target_median:
            lo = mid + 1
        else:
            hi = mid - 1

    print(
        f"  Auto threshold: {best_thresh:.1f} (target median={target_median:.0f}s, "
        f"actual median={_simulate(best_thresh):.0f}s)"
    )
    return best_thresh


def step1_scene_detect_segmentation(
    video_path: str,
    duration: float,
    output_dir: str | None = None,
    min_scene_sec: float = 30.0,
    max_scene_sec: float = 300.0,
    threshold: float = 0,
    start_sec: float = 0.0,
    end_sec: float | None = None,
) -> list[MacroEvent]:
    """Phase 1: Parallel frame-diff scene detection (no API calls)."""

    effective_end = end_sec if end_sec is not None else duration
    print(f"\n{'=' * 60}")
    print(
        f"Phase 1: Scene-Cut Segmentation — HLS frame-diff (threshold={threshold}, min={min_scene_sec}s, max={max_scene_sec}s)"
    )
    print(f"Video: {video_path}, Duration: {duration:.0f}s ({duration / 60:.1f}min)")
    print(f"Range: {sec_to_time_str(start_sec)} - {sec_to_time_str(effective_end)}")
    print(f"{'=' * 60}")

    all_macros: list[MacroEvent] = []
    resume_path = os.path.join(output_dir, "01_macros.json") if output_dir else ""
    if resume_path and os.path.exists(resume_path):
        with open(resume_path) as f:
            raw = json.load(f)
        for m in raw:
            all_macros.append(
                MacroEvent(
                    macro_id=m["macro_id"],
                    label=m["label"],
                    time_range=m["time_range"],
                    summary=m.get("summary", ""),
                )
            )
        if all_macros:
            print(f"  Loaded {len(all_macros)} macros from checkpoint, skipping detection")
            return all_macros

    import cv2
    import numpy as np

    seg_duration = effective_end - start_sec
    detect_fps = 0.25
    n_frames = max(4, int(seg_duration * detect_fps))
    timestamps = [start_sec + i / detect_fps for i in range(n_frames)]

    print(f"  Extracting {n_frames} frames @{detect_fps}fps via parallel seek ({FRAME_WORKERS} workers)...")
    t0 = time.time()

    def _extract_jpeg_frame(ts: float):
        return (ts, _ffmpeg_seek_jpeg(video_path, ts, "360:-2", q=10, timeout=30))

    with ThreadPoolExecutor(max_workers=FRAME_WORKERS) as pool:
        results = list(pool.map(_extract_jpeg_frame, timestamps))
    # pool.map preserves submission order and `timestamps` is strictly ascending, so the
    # None-filtered list is already time-sorted — no explicit re-sort needed.
    results = [(ts, data) for ts, data in results if data is not None]
    print(f"  Extracted {len(results)}/{n_frames} frames in {time.time() - t0:.1f}s")

    hls_frames: list[tuple[float, "np.ndarray"]] = []
    for ts, jpg_data in results:
        arr = np.frombuffer(jpg_data, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is not None:
            hls = cv2.cvtColor(bgr, cv2.COLOR_BGR2HLS).astype(np.float32)
            hls_frames.append((ts, hls))

    w_hue, w_sat, w_lum = 1.0, 1.0, 1.0
    cut_times: list[float] = []
    cut_scores: list[float] = []
    for i in range(1, len(hls_frames)):
        ts_prev, hls_prev = hls_frames[i - 1]
        ts_curr, hls_curr = hls_frames[i]
        if hls_prev.shape != hls_curr.shape:
            continue
        delta_h = float(np.mean(np.abs(hls_curr[:, :, 0] - hls_prev[:, :, 0])))
        delta_l = float(np.mean(np.abs(hls_curr[:, :, 1] - hls_prev[:, :, 1])))
        delta_s = float(np.mean(np.abs(hls_curr[:, :, 2] - hls_prev[:, :, 2])))
        score = delta_h * w_hue + delta_l * w_lum + delta_s * w_sat
        cut_times.append(ts_curr)
        cut_scores.append(score)

    base_threshold = min(threshold, 15.0) if threshold > 0 else 15.0
    n_above = sum(1 for s in cut_scores if s >= base_threshold)
    print(f"  Frame diffs computed: {len(cut_times)} pairs, {n_above} above base threshold={base_threshold:.1f}")

    if threshold <= 0 and cut_times:
        threshold = _find_optimal_threshold(
            cut_times,
            cut_scores,
            start_sec,
            effective_end,
            min_scene_sec,
            max_scene_sec,
        )
    elif threshold <= 0:
        threshold = DEFAULT_SCENE_THRESHOLD

    boundaries = [t for t, s in zip(cut_times, cut_scores) if s >= threshold]
    print(f"  Using threshold={threshold:.1f}: {len(boundaries)} cuts retained")

    merged_boundaries: list[float] = []
    last_b = start_sec
    for b in boundaries:
        if b - last_b >= min_scene_sec:
            merged_boundaries.append(b)
            last_b = b
    print(f"  After merging (min={min_scene_sec}s): {len(merged_boundaries)} boundaries")

    final_segments: list[tuple[float, float]] = []
    prev = start_sec
    for b in merged_boundaries:
        seg_dur = b - prev
        if seg_dur > max_scene_sec:
            n_parts = max(2, round(seg_dur / (max_scene_sec * 0.7)))
            part_dur = seg_dur / n_parts
            for j in range(n_parts):
                ps = prev + j * part_dur
                pe = prev + (j + 1) * part_dur if j < n_parts - 1 else b
                final_segments.append((ps, pe))
        else:
            final_segments.append((prev, b))
        prev = b

    last_dur = effective_end - prev
    if last_dur > max_scene_sec:
        n_parts = max(2, round(last_dur / (max_scene_sec * 0.7)))
        part_dur = last_dur / n_parts
        for j in range(n_parts):
            ps = prev + j * part_dur
            pe = prev + (j + 1) * part_dur if j < n_parts - 1 else effective_end
            final_segments.append((ps, pe))
    elif final_segments and last_dur < min_scene_sec:
        s, _ = final_segments.pop()
        final_segments.append((s, effective_end))
    else:
        final_segments.append((prev, effective_end))

    if not final_segments:
        final_segments = [(start_sec, effective_end)]

    for i, (s, e) in enumerate(final_segments):
        macro_id = f"macro_{i:04d}"
        macro = MacroEvent(
            macro_id=macro_id,
            label=f"scene_{i:04d}",
            time_range=[s, e],
            summary="",
        )
        all_macros.append(macro)
        print(f"  {macro_id}: [{sec_to_time_str(s)}-{sec_to_time_str(e)}] ({e - s:.0f}s)")

    if output_dir:
        _save_checkpoint(output_dir, "01_macros.json", all_macros)

    print(f"\nTotal Macro Events: {len(all_macros)}")
    _print_scene_stats(all_macros)
    return all_macros


def _print_scene_stats(macros: list[MacroEvent]):
    if not macros:
        return
    durations = [m.time_range[1] - m.time_range[0] for m in macros]
    avg = sum(durations) / len(durations)
    print(f"  Stats: {len(macros)} scenes, min={min(durations):.0f}s, max={max(durations):.0f}s, avg={avg:.0f}s")
    short = sum(1 for d in durations if d < 60)
    long = sum(1 for d in durations if d > 600)
    if short:
        print(f"  WARNING: {short} scenes shorter than 1 min")
    if long:
        print(f"  WARNING: {long} scenes longer than 10 min")


# ── ASR parallel pipeline ───────────────────────────────────────────────


def _extract_audio_chunk(video_path: str, out_path: str, start: float, duration: float):
    """Extract a WAV chunk from video using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        str(start),
        "-i",
        video_path,
        "-t",
        str(duration),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {proc.stderr.decode()[:200]}")


def _transcribe_chunk_dashscope(audio_path: str, model: str, api_key: str) -> list[str]:
    """Transcribe a single audio chunk via DashScope qwen3-asr."""
    import dashscope

    resp = dashscope.MultiModalConversation.call(
        model=model,
        messages=[{"role": "user", "content": [{"audio": Path(audio_path).expanduser().resolve().as_uri()}]}],
        result_format="message",
        api_key=api_key,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ASR failed ({resp.status_code}): {resp.code} - {resp.message}")
    content = resp.output.get("choices", [{}])[0].get("message", {}).get("content", [])
    sentences = []
    for item in content:
        text = item.get("text", "").strip() if isinstance(item, dict) else str(item).strip()
        if text:
            sentences.append(text)
    return sentences


def run_asr_parallel(
    video_path: str,
    output_dir: str,
    duration: float,
    model: str = "qwen3-asr-flash",
    api_key: str = "",
    chunk_sec: float = 30.0,
    max_workers: int = 8,
) -> list[dict]:
    """Run ASR transcription in parallel chunks. Returns [{start_sec, end_sec, text}, ...]."""
    asr_path = os.path.join(output_dir, "asr_transcript.json")
    if os.path.exists(asr_path):
        print(f"  ASR: Loading from checkpoint {asr_path}")
        with open(asr_path) as f:
            return json.load(f)

    print(f"\n{'=' * 60}")
    print(f"ASR: Parallel transcription (model={model}, chunk={chunk_sec}s, workers={max_workers})")
    print(f"{'=' * 60}")

    n_chunks = max(1, int(duration / chunk_sec) + (1 if duration % chunk_sec > 1 else 0))
    tmp_dir = os.path.join(output_dir, "_asr_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    def _process_chunk(i: int) -> dict | None:
        start = i * chunk_sec
        end = min(start + chunk_sec, duration)
        if end - start < 0.5:
            return None
        wav_path = os.path.join(tmp_dir, f"chunk_{i:04d}.wav")
        try:
            _extract_audio_chunk(video_path, wav_path, start, end - start)
            sentences = _transcribe_chunk_dashscope(wav_path, model, api_key)
            text = " ".join(sentences)
            if text.strip():
                return {"start_sec": start, "end_sec": end, "text": text}
        except Exception as e:
            print(f"  ASR chunk {i} ({sec_to_time_str(start)}-{sec_to_time_str(end)}): {e}")
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)
        return None

    transcript: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_chunk, i): i for i in range(n_chunks)}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                transcript.append(result)

    transcript.sort(key=lambda x: x["start_sec"])
    print(f"  ASR: {len(transcript)}/{n_chunks} chunks transcribed")

    # Save checkpoint
    os.makedirs(output_dir, exist_ok=True)
    with open(asr_path, "w") as f:
        json.dump(transcript, f, ensure_ascii=False, indent=2)
    print(f"  ASR: Saved to {asr_path}")

    # Cleanup tmp dir
    import shutil

    shutil.rmtree(tmp_dir, ignore_errors=True)

    return transcript


def merge_asr_into_macros(macros: list[MacroEvent], transcript: list[dict]):
    """Merge ASR transcript into macro events by time-range overlap."""
    if not transcript:
        return
    for macro in macros:
        ms, me = macro.time_range
        texts = []
        for seg in transcript:
            ss, se = seg["start_sec"], seg["end_sec"]
            if ss < me and se > ms:
                texts.append(seg["text"])
        if texts:
            macro.asr_text = " ".join(texts)


def _extract_one_subgraph(
    args: tuple,
) -> tuple[int, MacroEvent]:
    """Extract subgraph for a single macro event (progressive fallback on resolution)."""
    i, macro, video_path, model, api_key = args[:5]
    max_dim = args[5] if len(args) > 5 else 720
    start, end = macro.time_range
    duration = end - start

    context = (
        f"Video segment: {macro.label}\n"
        f"Absolute time in full video: {sec_to_time_str(start)} to {sec_to_time_str(end)} "
        f"(duration: {duration:.0f}s)\n"
        f"IMPORTANT: All time_range values in your output must be RELATIVE to this segment — "
        f"second 0 is the start of this clip, second {duration:.0f} is the end. "
        f"Do NOT use absolute video timestamps.\n"
    )
    if macro.asr_text:
        context += f"\nAudio transcript (ASR) for this segment:\n{macro.asr_text}\n"
    context += "\n"

    if _USE_OSS:
        fallback_configs = [
            (d, c)
            for d, c in [
                (1280, 24),
                (1024, 24),
                (768, 24),
                (512, 24),
                (384, 24),
            ]
            if d <= max_dim
        ]

        video_url = None
        for cfg_dim, cfg_crf in fallback_configs:
            video_url = clip_and_upload_video(video_path, start, end, max_dim=cfg_dim, crf=cfg_crf)
            if not video_url:
                print(f"  [{i}] {macro.macro_id}: failed to clip video, skipping")
                macro.subgraph = Subgraph(macro_id=macro.macro_id)
                return i, macro
            print(f"  [{i}] {macro.macro_id}: video clip @{cfg_dim}px crf={cfg_crf}, calling API...")
            try:
                resp = call_vlm_curl(
                    context + SUBGRAPH_CONSTRUCTION_PROMPT, video_url=video_url, model=model, api_key=api_key
                )
                break
            except Exception as exc:
                err_msg = str(exc)
                if "400 Bad Request" in err_msg:
                    print(f"  [{i}] {macro.macro_id}: bad request @{cfg_dim}px crf={cfg_crf}, fallback...")
                    continue
                raise
        else:
            print(f"  [{i}] {macro.macro_id}: all fallback configs exhausted")
            macro.subgraph = Subgraph(macro_id=macro.macro_id)
            return i, macro
    else:
        _fps = 0.5
        _dim = max_dim
        _max_fr = 250
        frame_items = extract_frames_base64(video_path, start, end, fps=_fps, max_dim=_dim, max_frames=_max_fr)
        if not frame_items:
            print(f"  [{i}] {macro.macro_id}: failed to extract frames, skipping")
            macro.subgraph = Subgraph(macro_id=macro.macro_id)
            return i, macro
        print(f"  [{i}] {macro.macro_id}: {len(frame_items)} frames (base64), calling API...")
        try:
            resp = call_vlm_curl(
                context + SUBGRAPH_CONSTRUCTION_PROMPT, frame_items=frame_items, model=model, api_key=api_key
            )
        except Exception as exc:
            print(f"  [{i}] {macro.macro_id}: API error: {exc}")
            macro.subgraph = Subgraph(macro_id=macro.macro_id)
            return i, macro
    try:
        sg_data = extract_json(resp)
    except json.JSONDecodeError:
        print(f"  [{i}] {macro.macro_id}: JSON parse failed")
        macro.subgraph = Subgraph(macro_id=macro.macro_id)
        return i, macro

    def _to_float(v):
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return time_str_to_sec(v)
        return 0.0

    events = []
    for e in sg_data.get("micro_events", []):
        tr = e.get("time_range", [start, end])
        if isinstance(tr, list) and len(tr) == 2:
            tr = [_to_float(tr[0]) + start, _to_float(tr[1]) + start]
        events.append(
            MicroEvent(
                event_id=e.get("event_id", f"ev_{i}"),
                event_type=e.get("event_type", ""),
                time_range=tr,
                subject=e.get("subject", ""),
                object=e.get("object", ""),
                action=e.get("action", ""),
                description=e.get("description", ""),
                macro_id=macro.macro_id,
            )
        )

    entities = []
    for e in sg_data.get("entities", []):
        vg = e.get("visual_grounding", {})
        if isinstance(vg, dict) and "primary_time_sec" in vg:
            vg["primary_time_sec"] = _to_float(vg["primary_time_sec"]) + start
        entities.append(
            Entity(
                entity_id=e.get("entity_id", f"ent_{i}"),
                name=e.get("name"),
                entity_type=e.get("entity_type", "OBJECT"),
                attributes=e.get("attributes", {}),
                description=e.get("description", ""),
                visual_grounding=vg if isinstance(vg, dict) else {},
                macro_id=macro.macro_id,
            )
        )

    edges = []
    for e in sg_data.get("edges", []):
        edges.append(
            Edge(
                source_id=str(e.get("source_id", "")),
                target_id=str(e.get("target_id", "")),
                relation_label=e.get("relation_label", ""),
                relation_type=e.get("relation_type", ""),
                description=e.get("description", ""),
            )
        )

    on_screen_texts = []
    for t in sg_data.get("on_screen_texts", []):
        tr = t.get("time_range", [start, end])
        if isinstance(tr, list) and len(tr) == 2:
            tr = [_to_float(tr[0]) + start, _to_float(tr[1]) + start]
        on_screen_texts.append(
            OnScreenText(
                text_id=t.get("text_id", f"ocr_{i}"),
                text=t.get("text", ""),
                time_range=tr,
                description=t.get("description", ""),
                macro_id=macro.macro_id,
            )
        )

    macro.subgraph = Subgraph(
        macro_id=macro.macro_id,
        micro_events=events,
        entities=entities,
        on_screen_texts=on_screen_texts,
        edges=edges,
    )
    macro.key_entities = [{"name": e.name or e.entity_id, "type": e.entity_type} for e in entities[:10]]
    macro.event_types = list({e.event_type for e in events if e.event_type})
    macro.ocr_texts = [t.text for t in on_screen_texts]

    print(
        f"  [{i}] {macro.macro_id}: {len(entities)} ent, {len(events)} ev, {len(on_screen_texts)} ocr, {len(edges)} edges ✓"
    )
    return i, macro


def _has_subgraph_content(macro: MacroEvent) -> bool:
    return bool(
        macro.subgraph and (macro.subgraph.entities or macro.subgraph.micro_events or macro.subgraph.on_screen_texts)
    )


def _load_prebuilt_subgraph(macro: MacroEvent, subgraph_dir: str) -> bool:
    """Load a pre-built subgraph JSON file if it exists."""
    sg_path = os.path.join(subgraph_dir, f"{macro.macro_id}.json")
    if not os.path.exists(sg_path):
        return False
    try:
        with open(sg_path) as f:
            sg_data = json.load(f)
        events = [
            MicroEvent(
                event_id=e.get("event_id", ""),
                event_type=e.get("event_type", ""),
                time_range=e.get("time_range", [0, 0]),
                subject=e.get("subject", ""),
                object=e.get("object", ""),
                action=e.get("action", ""),
                description=e.get("description", ""),
                macro_id=macro.macro_id,
            )
            for e in sg_data.get("micro_events", [])
        ]
        entities = [
            Entity(
                entity_id=e.get("entity_id", ""),
                name=e.get("name"),
                entity_type=e.get("entity_type", "OBJECT"),
                attributes=e.get("attributes", {}),
                description=e.get("description", ""),
                visual_grounding=e.get("visual_grounding", {}),
                macro_id=macro.macro_id,
            )
            for e in sg_data.get("entities", [])
        ]
        edges = [
            Edge(
                source_id=str(e.get("source_id", "")),
                target_id=str(e.get("target_id", "")),
                relation_label=e.get("relation_label", ""),
                relation_type=e.get("relation_type", ""),
                description=e.get("description", ""),
            )
            for e in sg_data.get("edges", [])
        ]
        on_screen_texts = [
            OnScreenText(
                text_id=t.get("text_id", ""),
                text=t.get("text", ""),
                time_range=t.get("time_range", []),
                description=t.get("description", ""),
                macro_id=macro.macro_id,
            )
            for t in sg_data.get("on_screen_texts", [])
        ]
        macro.subgraph = Subgraph(
            macro_id=macro.macro_id,
            micro_events=events,
            entities=entities,
            on_screen_texts=on_screen_texts,
            edges=edges,
        )
        macro.key_entities = [{"name": e.name or e.entity_id, "type": e.entity_type} for e in entities[:10]]
        macro.event_types = list({e.event_type for e in events if e.event_type})
        macro.ocr_texts = [t.text for t in on_screen_texts]
        return _has_subgraph_content(macro)
    except Exception:
        return False


def step2_subgraph_extraction(
    video_path: str,
    macros: list[MacroEvent],
    concurrency: int = 8,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    max_retries: int = 3,
    output_dir: str | None = None,
) -> list[MacroEvent]:
    """Phase 2: extract subgraphs for each macro event (concurrent, with retry)."""
    from concurrent.futures import ThreadPoolExecutor

    print(f"\n{'=' * 60}")
    print(f"Phase 2: Subgraph Extraction ({len(macros)} macro events, {concurrency} workers)")
    print(f"{'=' * 60}")

    _api_key = api_key or get_env("DASHSCOPE_API_KEY") or ""

    subgraph_dir = os.path.join(output_dir, "subgraphs") if output_dir else ""
    prebuilt = 0
    pending_indices = []
    for i in range(len(macros)):
        if subgraph_dir and _load_prebuilt_subgraph(macros[i], subgraph_dir):
            prebuilt += 1
        else:
            pending_indices.append(i)
    if prebuilt:
        print(f"  Loaded {prebuilt} pre-built subgraphs from worker, {len(pending_indices)} remaining")

    for attempt in range(max_retries + 1):
        if not pending_indices:
            break
        if attempt > 0:
            print(f"\n  Retry round {attempt}: {len(pending_indices)} macros to retry")

        tasks = [(i, macros[i], video_path, model, _api_key) for i in pending_indices]
        failed = []

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_extract_one_subgraph, t): t[0] for t in tasks}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    _, updated = fut.result()
                    macros[idx] = updated
                    if not _has_subgraph_content(updated):
                        failed.append(idx)
                except Exception as e:
                    print(f"  [{idx}] ERROR: {e}")
                    failed.append(idx)

        pending_indices = failed

    if pending_indices:
        print(f"  WARNING: {len(pending_indices)} macros still have empty subgraphs after {max_retries} retries")

    total_ent = sum(len(m.subgraph.entities) for m in macros if m.subgraph)
    total_ev = sum(len(m.subgraph.micro_events) for m in macros if m.subgraph)
    print(f"\nPhase 2 done: {total_ent} entities, {total_ev} events total")
    return macros


def step3_hierarchical_aggregation(
    macros: list[MacroEvent],
    model: str = DEFAULT_MODEL,
    api_key: str = "",
    window_size: int = 50,
) -> tuple[VideoRoot, list[SuperEvent], list[MacroRelation], list[SuperRelation]]:
    """Phase 3: aggregate macros into supers and root using sliding window.

    For <= window_size macros, uses a single pass (original behavior).
    For > window_size macros, uses sliding windows of window_size macros each.
    The last super_event from each window is dropped and its macros become the
    start of the next window, ensuring boundary continuity.
    After all windows, a root synthesis pass aggregates the super events.
    """
    print(f"\n{'=' * 60}")
    print(f"Phase 3: Hierarchical Aggregation (window_size={window_size})")
    print(f"{'=' * 60}")

    if len(macros) <= window_size:
        return _step3_single_pass(macros, model, api_key)

    return _step3_sliding_window(macros, model, api_key, window_size)


def _macro_to_dict(m: MacroEvent) -> dict:
    """Convert a MacroEvent to a dict for prompt serialization."""
    return {
        "macro_id": m.macro_id,
        "label": m.label,
        "time_range": [sec_to_time_str(t) for t in m.time_range],
        "summary": m.summary,
        "key_entities": m.key_entities,
        "event_types": m.event_types,
        "ocr_texts": m.ocr_texts,
    }


def _parse_window_result(
    agg: dict,
    macros: list[MacroEvent],
    super_counter: int,
) -> tuple[list[SuperEvent], list[MacroRelation], int]:
    """Parse a window aggregation result into SuperEvents and MacroRelations."""
    supers = []
    for se in agg.get("super_events", []):
        sid = f"super_{super_counter:02d}"
        super_counter += 1
        supers.append(
            SuperEvent(
                super_id=sid,
                label=se.get("label", ""),
                description=se.get("description", ""),
                sub_macro_ids=se.get("sub_macro_ids", []),
                time_range=_parse_time_range(se.get("time_range", [])),
                key_entities=se.get("key_entities", []),
            )
        )
        for mid in se.get("sub_macro_ids", []):
            for m in macros:
                if m.macro_id == mid:
                    m.super_id = sid

    macro_rels = [
        MacroRelation(source=r["source"], target=r["target"], type=r["type"], reason=r.get("reason", ""))
        for r in agg.get("macro_relations", [])
    ]
    return supers, macro_rels, super_counter


def _step3_single_pass(
    macros: list[MacroEvent],
    model: str,
    api_key: str,
) -> tuple[VideoRoot, list[SuperEvent], list[MacroRelation], list[SuperRelation]]:
    """Original single-pass aggregation for small macro counts."""
    print(f"  Single pass: {len(macros)} macros (within window)")

    macro_list = [_macro_to_dict(m) for m in macros]
    input_data = json.dumps(macro_list, ensure_ascii=False, indent=2)
    prompt = f"Here are the chronologically ordered Macro events:\n\n{input_data}\n\n" + HIERARCHICAL_AGGREGATION_PROMPT

    resp = call_vlm_curl(prompt, model=model, api_key=api_key)
    try:
        agg = extract_json(resp)
    except json.JSONDecodeError:
        print("  Failed to parse aggregation JSON, using fallback")
        agg = _fallback_aggregation(macros)

    root_data = agg.get("root", {})
    root = VideoRoot(
        title=root_data.get("title", ""),
        description=root_data.get("description", ""),
        themes=root_data.get("themes", []),
        key_entities=root_data.get("key_entities", []),
        emotional_tone=root_data.get("emotional_tone", ""),
    )

    supers = []
    for se in agg.get("super_events", []):
        sid = se.get("super_id", "")
        supers.append(
            SuperEvent(
                super_id=sid,
                label=se.get("label", ""),
                description=se.get("description", ""),
                sub_macro_ids=se.get("sub_macro_ids", []),
                time_range=_parse_time_range(se.get("time_range", [])),
                key_entities=se.get("key_entities", []),
            )
        )
        for mid in se.get("sub_macro_ids", []):
            for m in macros:
                if m.macro_id == mid:
                    m.super_id = sid

    macro_rels = [
        MacroRelation(source=r["source"], target=r["target"], type=r["type"], reason=r.get("reason", ""))
        for r in agg.get("macro_relations", [])
    ]
    super_rels = [
        SuperRelation(source=r["source"], target=r["target"], type=r["type"], reason=r.get("reason", ""))
        for r in agg.get("super_relations", [])
    ]

    _print_step3_summary(root, supers, macro_rels, super_rels)
    return root, supers, macro_rels, super_rels


def _step3_sliding_window(
    macros: list[MacroEvent],
    model: str,
    api_key: str,
    window_size: int,
) -> tuple[VideoRoot, list[SuperEvent], list[MacroRelation], list[SuperRelation]]:
    """Sliding window aggregation for large macro counts."""
    all_supers: list[SuperEvent] = []
    all_macro_rels: list[MacroRelation] = []
    super_counter = 0
    macro_ids_by_index = {m.macro_id: i for i, m in enumerate(macros)}

    window_start = 0
    window_num = 0

    while window_start < len(macros):
        window_end = min(window_start + window_size, len(macros))
        window_macros = macros[window_start:window_end]
        is_last_window = window_end >= len(macros)

        window_num += 1
        print(
            f"\n  Window {window_num}: macros[{window_start}:{window_end}] "
            f"({len(window_macros)} macros, {'final' if is_last_window else 'continuing'})"
        )

        macro_list = [_macro_to_dict(m) for m in window_macros]
        input_data = json.dumps(macro_list, ensure_ascii=False, indent=2)
        prompt = (
            f"Here are the chronologically ordered Macro events "
            f"(window {window_num}, macros {window_start} to {window_end - 1}):\n\n"
            f"{input_data}\n\n" + HIERARCHICAL_AGGREGATION_WINDOW_PROMPT
        )

        resp = call_vlm_curl(prompt, model=model, api_key=api_key)
        try:
            agg = extract_json(resp)
        except json.JSONDecodeError:
            print(f"  Window {window_num}: JSON parse failed, using fallback")
            agg = {
                "super_events": [
                    {
                        "super_id": f"super_{super_counter:02d}",
                        "label": f"Window {window_num}",
                        "description": ", ".join(m.label for m in window_macros),
                        "sub_macro_ids": [m.macro_id for m in window_macros],
                        "time_range": [window_macros[0].time_range[0], window_macros[-1].time_range[1]],
                        "key_entities": [],
                    }
                ],
                "macro_relations": [],
            }

        window_supers, window_rels, super_counter = _parse_window_result(agg, macros, super_counter)

        if not window_supers:
            print(f"  Window {window_num}: no super events returned, advancing")
            window_start = window_end
            continue

        for se in window_supers:
            print(f"    {se.super_id}: {se.label} ({len(se.sub_macro_ids)} macros)")

        if is_last_window or len(window_supers) <= 1:
            all_supers.extend(window_supers)
            all_macro_rels.extend(window_rels)
            window_start = window_end
        else:
            dropped = window_supers[-1]
            kept = window_supers[:-1]
            all_supers.extend(kept)
            super_counter -= 1

            kept_macro_ids = set()
            for se in kept:
                kept_macro_ids.update(se.sub_macro_ids)
            all_macro_rels.extend(r for r in window_rels if r.source in kept_macro_ids and r.target in kept_macro_ids)

            dropped_macro_ids = dropped.sub_macro_ids
            if dropped_macro_ids:
                first_dropped_id = dropped_macro_ids[0]
                if first_dropped_id in macro_ids_by_index:
                    # Rewind to the dropped super's first macro so the next window re-covers it,
                    # but always advance by at least one — a model that returns sub_macro_ids out of
                    # order could otherwise point back at/behind window_start and hang the loop.
                    window_start = max(macro_ids_by_index[first_dropped_id], window_start + 1)
                else:
                    window_start = window_end
            else:
                window_start = window_end

            for mid in dropped_macro_ids:
                for m in macros:
                    if m.macro_id == mid:
                        m.super_id = ""

            print(
                f"  Dropped last super '{dropped.label}' ({len(dropped_macro_ids)} macros), "
                f"next window from macro index {window_start}"
            )

    print(f"\n  Sliding window done: {len(all_supers)} super events from {window_num} windows")
    print(f"  Synthesizing root from {len(all_supers)} super events...")

    root, super_rels = _synthesize_root(all_supers, model, api_key)

    _print_step3_summary(root, all_supers, all_macro_rels, super_rels)
    return root, all_supers, all_macro_rels, super_rels


def _synthesize_root(
    supers: list[SuperEvent],
    model: str,
    api_key: str,
) -> tuple[VideoRoot, list[SuperRelation]]:
    """Synthesize root summary and super relations from all super events."""
    super_list = []
    for se in supers:
        super_list.append(
            {
                "super_id": se.super_id,
                "label": se.label,
                "description": se.description,
                "time_range": [sec_to_time_str(t) for t in se.time_range],
                "key_entities": se.key_entities,
            }
        )

    input_data = json.dumps(super_list, ensure_ascii=False, indent=2)
    prompt = f"Here are the chronologically ordered Super Events:\n\n{input_data}\n\n" + ROOT_SYNTHESIS_PROMPT

    resp = call_vlm_curl(prompt, model=model, api_key=api_key)
    try:
        result = extract_json(resp)
    except json.JSONDecodeError:
        print("  Root synthesis JSON parse failed, using fallback")
        result = {
            "root": {
                "title": "Video Summary",
                "description": f"Video with {len(supers)} super events",
                "themes": [],
                "key_entities": [],
                "emotional_tone": "",
            },
            "super_relations": [],
        }

    root_data = result.get("root", {})
    root = VideoRoot(
        title=root_data.get("title", ""),
        description=root_data.get("description", ""),
        themes=root_data.get("themes", []),
        key_entities=root_data.get("key_entities", []),
        emotional_tone=root_data.get("emotional_tone", ""),
    )

    super_rels = [
        SuperRelation(source=r["source"], target=r["target"], type=r["type"], reason=r.get("reason", ""))
        for r in result.get("super_relations", [])
    ]

    return root, super_rels


def _print_step3_summary(root, supers, macro_rels, super_rels):
    print(f"  Root: {root.title}")
    print(f"  Super Events: {len(supers)}")
    for se in supers:
        print(f"    {se.super_id}: {se.label} ({len(se.sub_macro_ids)} macros)")
    print(f"  Macro Relations: {len(macro_rels)}")
    print(f"  Super Relations: {len(super_rels)}")


def _parse_time_range(tr: list) -> list[float]:
    if not tr:
        return [0, 0]
    result = []
    for t in tr:
        if isinstance(t, (int, float)):
            result.append(float(t))
        elif isinstance(t, str):
            result.append(time_str_to_sec(t))
        else:
            result.append(0)
    return result


def _fallback_aggregation(macros: list[MacroEvent]) -> dict:
    chunk_size = max(1, len(macros) // 10)
    supers = []
    for i in range(0, len(macros), chunk_size):
        chunk = macros[i : i + chunk_size]
        sid = f"super_{i // chunk_size:02d}"
        supers.append(
            {
                "super_id": sid,
                "label": f"Phase {i // chunk_size + 1}",
                "description": ", ".join(m.label for m in chunk),
                "sub_macro_ids": [m.macro_id for m in chunk],
                "time_range": [chunk[0].time_range[0], chunk[-1].time_range[1]],
                "key_entities": [],
            }
        )
    return {
        "super_events": supers,
        "macro_relations": [],
        "super_relations": [],
        "root": {
            "title": "Video Summary",
            "description": f"Video with {len(macros)} macro events",
            "themes": [],
            "key_entities": [],
            "emotional_tone": "",
        },
    }


def _assemble_save_embed(
    video_key: str,
    video_path: str,
    output_dir: str,
    root,
    supers,
    macros,
    macro_rels,
    super_rels,
) -> HierarchicalGraphMemory:
    """Assemble memory, save graph_memory.json, build + save embeddings."""
    memory = HierarchicalGraphMemory(
        video_key=video_key or Path(video_path).stem,
        video_path=video_path,
        root=root,
        super_events=supers,
        macro_events=macros,
        macro_relations=macro_rels,
        super_relations=super_rels,
    )

    graph_path = os.path.join(output_dir, "graph_memory.json")
    memory.save(graph_path)
    print(f"\nGraph memory saved to: {graph_path}")

    _build_save_embeddings(memory, output_dir)
    return memory


def _build_save_embeddings(memory: HierarchicalGraphMemory, output_dir: str) -> str:
    """Build the embedding index and replace its output only after a complete save."""
    print("\nPhase 4: Building embedding index...")
    index = EmbeddingIndex()
    nodes = memory.get_all_nodes()
    if not nodes:
        raise RuntimeError("graph contains no nodes; cannot build embeddings")
    index.build(nodes)

    index_path = os.path.join(output_dir, "embeddings.npz")
    index.save(index_path)
    print(f"Embedding index saved to: {index_path} ({len(nodes)} nodes)")
    return index_path


def build_graph_memory(
    video_path: str,
    video_key: str = "",
    output_dir: str = "",
    concurrency: int = 60,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    min_scene_sec: float = 30.0,
    max_scene_sec: float = 300.0,
    scene_threshold: float = 0,
    enable_asr: bool = True,
    asr_model: str = "qwen3-asr-flash",
) -> HierarchicalGraphMemory:
    """End-to-end graph memory construction."""
    if not output_dir:
        output_dir = os.path.abspath(video_path) + ".memory"
    os.makedirs(output_dir, exist_ok=True)
    _api_key = api_key or get_env("DASHSCOPE_API_KEY") or ""

    duration = get_video_duration(video_path)
    print(f"Video duration: {duration:.0f}s ({duration / 60:.1f}min)")

    # Phase 1 + ASR in parallel
    checkpoint1 = os.path.join(output_dir, "01_macros.json")
    done_marker = os.path.join(output_dir, "01_done")

    asr_future = None
    if enable_asr:
        asr_pool = ThreadPoolExecutor(max_workers=1)
        asr_future = asr_pool.submit(
            run_asr_parallel,
            video_path,
            output_dir,
            duration,
            model=asr_model,
            api_key=_api_key,
        )

    if os.path.exists(done_marker) and os.path.exists(checkpoint1):
        print(f"Loading Phase 1 checkpoint: {checkpoint1}")
        with open(checkpoint1) as f:
            raw = json.load(f)
        macros = [
            MacroEvent(
                macro_id=m["macro_id"],
                label=m["label"],
                time_range=m["time_range"],
                summary=m.get("summary", ""),
            )
            for m in raw
        ]
        print(f"  Loaded {len(macros)} macro events from checkpoint")
    elif not os.path.exists(done_marker):
        macros = step1_scene_detect_segmentation(
            video_path,
            duration,
            output_dir=output_dir,
            min_scene_sec=min_scene_sec,
            max_scene_sec=max_scene_sec,
            threshold=scene_threshold,
        )
        _save_checkpoint(output_dir, "01_macros.json", macros)
        Path(done_marker).touch()

    # Collect ASR results
    transcript = []
    if asr_future is not None:
        try:
            transcript = asr_future.result()
            merge_asr_into_macros(macros, transcript)
            print(f"  ASR: Merged {sum(1 for m in macros if m.asr_text)}/{len(macros)} macros with transcript")
        except Exception as e:
            print(f"  ASR failed (non-fatal): {e}")
        finally:
            asr_pool.shutdown(wait=False)

    # Phase 2: Subgraph extraction
    macros = step2_subgraph_extraction(
        video_path,
        macros,
        concurrency=concurrency,
        model=model,
        api_key=_api_key,
        output_dir=output_dir,
    )
    _save_checkpoint(output_dir, "02_macros_with_subgraphs.json", macros)

    # Phase 3: Hierarchical aggregation
    root, supers, macro_rels, super_rels = step3_hierarchical_aggregation(macros, model=model, api_key=_api_key)

    return _assemble_save_embed(video_key, video_path, output_dir, root, supers, macros, macro_rels, super_rels)


def _save_subgraph_compat(output_dir: str, macro: MacroEvent):
    """Save subgraph as subgraphs/macro_XXXX.json (backward-compatible format)."""
    from dataclasses import asdict

    sg_dir = os.path.join(output_dir, "subgraphs")
    os.makedirs(sg_dir, exist_ok=True)
    if macro.subgraph:
        sg_path = os.path.join(sg_dir, f"{macro.macro_id}.json")
        with open(sg_path, "w") as f:
            json.dump(asdict(macro.subgraph), f, ensure_ascii=False, indent=2)


def _save_checkpoint(output_dir: str, filename: str, data):
    import dataclasses

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(
            [dataclasses.asdict(d) if dataclasses.is_dataclass(d) else d for d in data],
            f,
            ensure_ascii=False,
            indent=2,
        )
    os.replace(tmp, path)
    print(f"  Checkpoint saved: {path}")


def merge_chunks(chunk_dirs: list[str], output_dir: str) -> int:
    """Merge parallel per-chunk outputs into one build dir.

    Concatenates each chunk's ``01_macros.json``, re-sorts globally by time_range start,
    reassigns contiguous ``macro_XXXX`` ids, and copies each subgraph file under its new id
    (rewriting the subgraph's own ``macro_id``). Returns the merged macro count. Operates on
    the on-disk JSON that ``--phase3-only`` then loads. (Extracted from build_memory.sh so it
    can be unit-tested and type-checked instead of living in a bash heredoc.)
    """
    all_macros: list[tuple[str, dict]] = []
    for cd in sorted(chunk_dirs):
        path = os.path.join(cd, "01_macros.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for m in json.load(f):
                all_macros.append((cd, m))

    all_macros.sort(key=lambda x: x[1]["time_range"][0])

    merged: list[dict] = []
    subgraph_dir = os.path.join(output_dir, "subgraphs")
    os.makedirs(subgraph_dir, exist_ok=True)

    for idx, (cd, m) in enumerate(all_macros):
        old_id = m["macro_id"]
        new_id = f"macro_{idx:04d}"
        m["macro_id"] = new_id
        merged.append(m)

        old_path = os.path.join(cd, "subgraphs", f"{old_id}.json")
        new_path = os.path.join(subgraph_dir, f"{new_id}.json")
        if os.path.exists(old_path) and not os.path.exists(new_path):
            with open(old_path) as f:
                sg = json.load(f)
            if "macro_id" in sg:
                sg["macro_id"] = new_id
            with open(new_path, "w") as f:
                json.dump(sg, f, ensure_ascii=False)

    with open(os.path.join(output_dir, "01_macros.json"), "w") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    return len(merged)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build graph memory")
    parser.add_argument("video_path", help="Path to video file")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: <video_path>.memory)")
    parser.add_argument("--video-key", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--phase1-only", action="store_true", help="Run only Phase 1 (segmentation), then exit")
    parser.add_argument(
        "--phase3-only", action="store_true", help="Run only Phase 3 (load subgraphs + aggregation + embeddings)"
    )
    parser.add_argument(
        "--embeddings-only", action="store_true", help="Build embeddings from an existing graph_memory.json"
    )
    parser.add_argument("--start-sec", type=float, default=0.0, help="Start time in seconds for Phase 1 (default: 0)")
    parser.add_argument(
        "--end-sec", type=float, default=None, help="End time in seconds for Phase 1 (default: full duration)"
    )
    parser.add_argument(
        "--merge-chunks",
        nargs="+",
        metavar="CHUNK_DIR",
        help="Merge these parallel chunk dirs' macros + subgraphs into --output-dir, then exit",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=0,
        help="scene-cut (HLS frame-diff) threshold (default: 0 = auto-select)",
    )
    parser.add_argument(
        "--min-scene-sec", type=float, default=30.0, help="Minimum scene duration in seconds (default: 30)"
    )
    parser.add_argument(
        "--max-scene-sec", type=float, default=300.0, help="Maximum scene duration in seconds (default: 300)"
    )
    # ASR options
    parser.add_argument(
        "--asr", action="store_true", default=True, help="Enable parallel ASR transcription (default: True)"
    )
    parser.add_argument("--no-asr", dest="asr", action="store_false", help="Disable ASR transcription")
    parser.add_argument("--asr-model", default="qwen3-asr-flash", help="ASR model name (default: qwen3-asr-flash)")
    args = parser.parse_args()

    # Resolve the default output dir once, so every branch (merge-chunks / phase1 / phase3 /
    # full build) sees it — not just build_graph_memory().
    if args.output_dir is None:
        args.output_dir = os.path.abspath(args.video_path) + ".memory"

    _api_key = args.api_key or get_env("DASHSCOPE_API_KEY") or ""

    if args.merge_chunks:
        n = merge_chunks(args.merge_chunks, args.output_dir)
        print(f"  Merged {n} macros from {len(args.merge_chunks)} chunks")
        print(f"  Subgraphs copied to {os.path.join(args.output_dir, 'subgraphs')}")
        sys.exit(0)

    if args.embeddings_only:
        graph_path = os.path.join(args.output_dir, "graph_memory.json")
        if not os.path.exists(graph_path):
            print(f"ERROR: {graph_path} not found")
            sys.exit(1)
        _build_save_embeddings(HierarchicalGraphMemory.load(graph_path), args.output_dir)

    elif args.phase1_only:
        os.makedirs(args.output_dir, exist_ok=True)
        duration = get_video_duration(args.video_path)
        print(f"Video duration: {duration:.0f}s ({duration / 60:.1f}min)")

        asr_future = None
        if args.asr:
            asr_pool = ThreadPoolExecutor(max_workers=1)
            asr_future = asr_pool.submit(
                run_asr_parallel,
                args.video_path,
                args.output_dir,
                duration,
                model=args.asr_model,
                api_key=_api_key,
            )

        macros = step1_scene_detect_segmentation(
            args.video_path,
            duration,
            output_dir=args.output_dir,
            min_scene_sec=args.min_scene_sec,
            max_scene_sec=args.max_scene_sec,
            threshold=args.scene_threshold,
            start_sec=args.start_sec,
            end_sec=args.end_sec,
        )

        if asr_future is not None:
            try:
                transcript = asr_future.result()
                merge_asr_into_macros(macros, transcript)
                print(f"  ASR: Merged {sum(1 for m in macros if m.asr_text)}/{len(macros)} macros with transcript")
            except Exception as e:
                print(f"  ASR failed (non-fatal): {e}")
            finally:
                asr_pool.shutdown(wait=False)

        _save_checkpoint(args.output_dir, "01_macros.json", macros)
        Path(os.path.join(args.output_dir, "01_done")).touch()
        print(f"\nPhase 1 complete: {len(macros)} macro events")

    elif args.phase3_only:
        os.makedirs(args.output_dir, exist_ok=True)
        checkpoint1 = os.path.join(args.output_dir, "01_macros.json")
        if not os.path.exists(checkpoint1):
            print(f"ERROR: {checkpoint1} not found")
            sys.exit(1)
        with open(checkpoint1) as f:
            raw = json.load(f)
        macros = [
            MacroEvent(
                macro_id=m["macro_id"],
                label=m["label"],
                time_range=m["time_range"],
                summary=m.get("summary", ""),
                asr_text=m.get("asr_text", ""),
            )
            for m in raw
        ]
        print(f"Loaded {len(macros)} macro events from checkpoint")

        subgraph_dir = os.path.join(args.output_dir, "subgraphs")
        loaded = sum(1 for m in macros if _load_prebuilt_subgraph(m, subgraph_dir))
        print(f"Loaded {loaded}/{len(macros)} pre-built subgraphs")

        missing = [m for m in macros if not _has_subgraph_content(m)]
        if missing:
            print(f"Extracting {len(missing)} remaining subgraphs...")
            macros = step2_subgraph_extraction(
                args.video_path,
                macros,
                concurrency=60,
                model=args.model,
                api_key=_api_key,
                output_dir=args.output_dir,
            )
        _save_checkpoint(args.output_dir, "02_macros_with_subgraphs.json", macros)

        root, supers, macro_rels, super_rels = step3_hierarchical_aggregation(
            macros,
            model=args.model,
            api_key=_api_key,
        )
        _assemble_save_embed(
            args.video_key, args.video_path, args.output_dir, root, supers, macros, macro_rels, super_rels
        )

    else:
        build_graph_memory(
            video_path=args.video_path,
            video_key=args.video_key,
            output_dir=args.output_dir,
            model=args.model,
            api_key=args.api_key,
            min_scene_sec=args.min_scene_sec,
            max_scene_sec=args.max_scene_sec,
            scene_threshold=args.scene_threshold,
            enable_asr=args.asr,
            asr_model=args.asr_model,
        )
