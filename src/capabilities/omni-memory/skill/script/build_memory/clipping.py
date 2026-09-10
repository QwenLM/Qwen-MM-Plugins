"""Turning a video into the windows a build ingests: the slice plan, and the ffmpeg calls behind one clip.

Windows are 30s on a 25s step, so consecutive clips overlap by 5s. That overlap is load-bearing: it is
what lets the stateful driver carry identities across a cut instead of re-deriving them, so a person
keeps one person_id for the whole video.

Only the build path is here. The query side re-encodes video too (watch_and_answer), but to a different
end — a whole video inside one request's payload budget — so it keeps its own encoder in mem_core.
"""

import os
import subprocess
import time

from omni_core import diag, probe_duration

INTER_CLIP_SLEEP = 1.5  # seconds between clips to smooth request rate (adaptive on throttling)


def plan_windows(duration, window=30, step=25, max_clips=0):
    """Plan overlapping windows over [0, duration]. overlap = window - step (=5 by default).
    Returns list of {idx, win_start, win_end, overlap} in absolute seconds."""
    overlap = max(0, window - step)
    wins, idx, start = [], 0, 0.0
    while start < max(duration - 0.5, 0.5):
        end = min(start + window, duration) if duration else start + window
        wins.append({"idx": idx, "win_start": round(start, 3), "win_end": round(end, 3), "overlap": overlap})
        if duration and end >= duration:
            break
        idx += 1
        start = round(start + step, 3)
    if max_clips and len(wins) > max_clips:
        wins = wins[:max_clips]
    return wins


def _clip_one(src, out_path, win_start, win_len, height=480, vbitrate="800k"):
    """Cut a single window. height=None keeps ORIGINAL resolution (no scale)."""
    last_err = ""
    vf = ["-vf", f"scale=-2:{height}"] if height else []
    for vcodec in ("libx264", "libopenh264"):
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(win_start),
            "-i",
            src,
            "-t",
            str(win_len),
            *vf,
            "-c:v",
            vcodec,
            "-b:v",
            vbitrate,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return True
        last_err = (r.stderr or "")[-300:]
    raise RuntimeError(f"ffmpeg window cut failed (libx264/libopenh264): {last_err}")


def plan_clips(src, window=30, step=25, max_clips=0):
    """Probe duration + plan sliding windows. Returns (duration, [window dicts]). Fast, no encode."""
    dur = probe_duration(src)
    plans = plan_windows(dur, window=window, step=step, max_clips=max_clips)
    diag(
        f"[CLIP] duration={dur:.1f}s -> {len(plans)} window(s) (win={window}/step={step}/max={max_clips or 'all'})",
        flush=True,
    )
    return dur, plans


def cut_window(src, out_dir, w, height=480):
    """Cut ONE planned window into a clip file. Returns a clip dict. Logs to stdout ([CLIP])."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"win_{w['idx']:03d}.mp4")
    win_len = round(w["win_end"] - w["win_start"], 3) or 30
    t = time.time()
    _clip_one(src, out_path, w["win_start"], win_len, height)
    diag(
        f"[CLIP] win {w['idx']} [{w['win_start']}-{w['win_end']}]s done in "
        f"{time.time() - t:.1f}s ({os.path.getsize(out_path) // 1024}KB)",
        flush=True,
    )
    return {
        "idx": w["idx"],
        "path": out_path,
        "win_start": w["win_start"],
        "win_end": w["win_end"],
        "overlap": w["overlap"],
    }


def cut_window_original(src, out_dir, w):
    """Store a full-resolution clip for replay via STREAM COPY (no re-encode — start snaps to the
    nearest keyframe, fine for re-watching). Much lighter than re-encoding. Returns local path."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"orig_{w['idx']:03d}.mp4")
    win_len = round(w["win_end"] - w["win_start"], 3) or 30
    t = time.time()
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        str(w["win_start"]),
        "-i",
        src,
        "-t",
        str(win_len),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not (os.path.exists(out_path) and os.path.getsize(out_path) > 0):
        raise RuntimeError(f"ffmpeg original -c copy failed: {(r.stderr or '')[-200:]}")
    diag(
        f"[CLIP] win {w['idx']} original COPY in {time.time() - t:.1f}s ({os.path.getsize(out_path) // 1024}KB)",
        flush=True,
    )
    return out_path
