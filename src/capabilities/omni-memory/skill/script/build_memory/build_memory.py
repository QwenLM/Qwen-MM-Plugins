#!/usr/bin/env python3
"""Build omni-memory for a long video. Long-running and serial — run it in the background.

Why this is a script and not an MCP tool: one video takes tens of minutes, the per-clip extraction
is strictly serial (identity/event state is threaded from clip to clip, so it cannot be parallelised
inside a video), and an interrupted run must be resumable. A script owns its own process, so it
survives the MCP server being restarted by the harness.

Two stages per video:
  1. SLICE   — plan 30s/25s windows and cut them into a PERSISTENT cache
               (<mdir>/clips/w30_s25_h480/<video_stem>/), with plan.json alongside. Persistent on
               purpose: replay re-watches these files at answer time, so they must outlive the
               build — a temp dir would silently break replay later.
  2. INGEST  — hand that cache to pipeline.build_memory, which extracts each clip statefully.

Usage:
  # one video → memory next to it, at <video>.memory/
  python3 build_memory.py VIDEO

  # STREAMING: several videos → ONE continuous memory. Identities keep their person_id and
  # semantic facts keep accumulating across videos; the timeline is stitched end to end.
  python3 build_memory.py --video-dir DIR --namespace my_stream
  python3 build_memory.py NEXT_VIDEO --namespace my_stream --mode append   # extend it later

  # many videos → independent per-video memories, built in parallel
  python3 build_memory.py --video-dir DIR -j 4
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_HERE = Path(__file__).resolve()
# There is deliberately no sys.path setup here. Every module this script needs is a sibling in this
# directory, and python already puts a script's own directory on sys.path. Nothing is imported from
# the server package: several harnesses install a skill by copying skill/ alone, so a path that
# climbs out of it points at nothing on those machines and the build dies before its first clip.

# The build runs under whatever python3 invoked it. Under a plugin install that is the system
# interpreter, while the capability's dependencies live in the harness's uvx environment — so they
# are simply not here. Check before the sibling imports below, which need both, and fail with the
# reason rather than a bare ModuleNotFoundError. No index is pinned: whatever pip is configured to
# use is the right answer on the machine we happen to be on.
_RUNTIME_PACKAGES = {"numpy": "numpy<3", "openai": "openai"}
_MISSING = [module for module in _RUNTIME_PACKAGES if importlib.util.find_spec(module) is None]
if _MISSING:
    packages = [_RUNTIME_PACKAGES[module] for module in _MISSING]
    print(f"[BUILD] installing missing packages: {' '.join(packages)}", file=sys.stderr, flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *packages], check=False)
    _STILL = [m for m in _MISSING if importlib.util.find_spec(m) is None]
    if _STILL:
        sys.exit(
            f"[BUILD] cannot run: pip could not install {' '.join(_STILL)}.\n"
            f"        Install them into {sys.executable} and retry."
        )

# Which MEM_* the ENVIRONMENT brought in, captured before the defaults below land on top of it.
# The tuning knobs these modules read are deliberately undocumented, so an exported one is more
# likely to be a name collision than an intention — and it changes retrieval or determinism silently,
# leaving a build whose numbers moved for no visible reason. Reported at startup, where the log
# keeps it. Only what came from outside is listed; the defaults set here are not news.
_ENV_TUNING = {k: v for k, v in sorted(os.environ.items()) if k.startswith("MEM_")}

os.environ.setdefault("MEM_ANON_ENTITIES", "1")  # extraction never binds names; alignment does
os.environ.setdefault("MEM_TEMPERATURE", "0")

import clipping  # noqa: E402
import env_config as config  # noqa: E402
import omni_core  # noqa: E402
import pipeline  # noqa: E402
import storage  # noqa: E402

MEMORY_SUFFIX = ".memory"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


class _Tee:
    """Write to the terminal and to the log file at once."""

    def __init__(self, stream, sink):
        self._stream, self._sink = stream, sink

    def write(self, s):
        self._stream.write(s)
        self._sink.write(s)
        self._sink.flush()
        return len(s)

    def flush(self):
        self._stream.flush()
        self._sink.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def start_logging(path: Path) -> Path:
    """Send both streams to `path` as well as to the terminal, and return where it went.

    The script's own stage progress goes to stdout while the memory core's per-clip and retry lines go
    to stderr (the core is also imported by the MCP server, whose stdout carries JSON-RPC and must stay
    clean). Splitting a 40-minute build's log across two streams and asking every caller to remember
    `2>&1` is a trap — especially in the background, where `> log &` silently drops the interesting
    half. Tee'ing here means the full log exists no matter how the script was invoked.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sink = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.stdout, sink)
    sys.stderr = _Tee(sys.stderr, sink)
    return path


def fingerprint(window: int, step: int, height: int) -> str:
    """Clip cache is only reusable for the same slicing parameters — put them in the dir name."""
    return f"w{window}_s{step}_h{height}"


def memory_dir(video: Path) -> Path:
    return Path(str(video.resolve()) + MEMORY_SUFFIX)


def slice_video(
    video: Path, mdir: Path, window: int, step: int, height: int, max_clips: int = 0
) -> tuple[Path, list[dict]]:
    """Cut sliding windows into a persistent cache; reuse an existing complete one as-is.

    Layout is dictated by what pipeline.build_memory expects from clips_dir — it looks for
    <clips_dir>/<video_stem>/plan.json — plus a parameter fingerprint, since clips are only
    reusable for the same window/step/height:

        <mdir>/clips/w30_s25_h480/<video_stem>/{plan.json, win_000.mp4, …}

    The per-video level also makes streaming work: several videos appended into one memory each
    keep their own clips under the same fingerprint dir.

    Returns (clips_root, plan) where clips_root is the path to hand to build_memory(clips_dir=…).
    """
    clips_root = mdir / "clips" / fingerprint(window, step, height)
    cdir = clips_root / video.stem
    plan_path = cdir / "plan.json"

    if plan_path.is_file():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Everything an unreadable cache can raise, and nothing else: OSError from the read,
            # ValueError for both JSONDecodeError and the UnicodeDecodeError a truncated write leaves
            # behind. Narrower than it was, so a bug of ours surfaces instead of quietly re-slicing;
            # not narrowed to JSONDecodeError alone, which would let that half-written file through.
            plan = []
        if plan and all(Path(c["path"]).is_file() for c in plan):
            print(f"[SLICE] cache hit: {len(plan)} clips at {cdir} — skipping ffmpeg", flush=True)
            return clips_root, plan
        print(f"[SLICE] cache incomplete at {cdir} — re-slicing", flush=True)

    cdir.mkdir(parents=True, exist_ok=True)
    dur, windows = clipping.plan_clips(str(video), window=window, step=step, max_clips=int(max_clips))
    if not windows:
        raise RuntimeError(f"cannot plan clips for {video} (unreadable or zero-length?)")
    print(
        f"[SLICE] {video.name}: {dur:.0f}s → {len(windows)} windows ({window}s/{step}s, {height}p) → {cdir}", flush=True
    )

    plan, t0 = [], time.time()
    for w in windows:
        out = cdir / f"win_{w['idx']:03d}.mp4"
        if out.is_file() and out.stat().st_size > 0:
            c = {
                "idx": w["idx"],
                "path": str(out),
                "win_start": w["win_start"],
                "win_end": w["win_end"],
                "overlap": w.get("overlap", window - step),
            }
        else:
            c = clipping.cut_window(str(video), str(cdir), w, height=height)
            c = {
                "idx": c["idx"],
                "path": c["path"],
                "win_start": c["win_start"],
                "win_end": c["win_end"],
                "overlap": c.get("overlap", window - step),
            }
        plan.append(c)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    size_mb = sum(Path(c["path"]).stat().st_size for c in plan) / 1048576
    print(f"[SLICE] done: {len(plan)} clips in {time.time() - t0:.0f}s ({size_mb:.0f} MB)", flush=True)
    return clips_root, plan


def build_one(
    video: Path, mode: str, namespace: str | None, window: int, step: int, height: int, rollup_k: int, max_clips: int
) -> dict:
    # The backend lays a memory out as <root>/<ns>/{store.json,meta.json,clips/}. Two
    # layouts, matching what the query tools accept (service.memory_dir):
    #   · default      → root = the video's directory, ns = "<video>.memory"
    #                    ⇒ everything lands inside <video>.memory/, next to the video
    #   · --namespace  → root = MEM_LOCAL_DIR when configured, otherwise the video's directory;
    #                    ns = that name
    if namespace:
        # Through config.local_dir(), NOT os.environ: MEM_LOCAL_DIR may live in the user config file
        # rather than the process environment. With no override, keep the memory beside the video so
        # the default is portable across operating systems and machines.
        root = Path(config.local_dir()) if config.local_dir() else video.parent
        ns, mdir = namespace, root / namespace
    else:
        mdir = memory_dir(video)
        root, ns = mdir.parent, mdir.name
    mdir.mkdir(parents=True, exist_ok=True)
    # Load-bearing for the default layout, where root is the VIDEO's directory: this is the only way
    # storage.get_backend() learns it. Removing it would root every per-video build at MEM_LOCAL_DIR.
    os.environ["MEM_LOCAL_DIR"] = str(root)
    backend = storage.get_backend()

    existing = ns in backend.list_namespaces()
    if existing and mode == "new":
        st = backend.load(ns)
        print(
            f"[BUILD] {ns}: memory already exists ({len(st.episodic)} clips). Use --mode append to "
            f"continue the timeline with this video, or --mode rebuild to start over.",
            flush=True,
        )
        return {"namespace": ns, "status": "exists", "episodic": len(st.episodic)}
    if mode == "append" and not existing:
        mode = "new"  # nothing to append to yet — this video starts the timeline

    clips_root, plan = slice_video(video, mdir, window, step, height, max_clips)

    if mode == "resume":
        if not existing:
            mode = "new"  # nothing to resume — build it from the start
        else:
            # Count what is already ingested FOR THIS VIDEO, not the whole library: with several
            # videos streamed into one memory, len(store.episodic) is the running total and would
            # make this look complete when it is not.
            have = {o.get("idx") for o in backend.load(ns).episodic}
            done = sum(1 for c in plan if c["idx"] in have)
            if done >= len(plan):
                print(f"[BUILD] {ns}: this video is already complete ({done}/{len(plan)})", flush=True)
                return {"namespace": ns, "status": "complete", "episodic": done, "planned": len(plan)}
            print(f"[BUILD] {ns}: resuming at clip {done}/{len(plan)} — {len(plan) - done} to go", flush=True)

    # Completeness baseline. append: store.episodic keeps growing across videos, so add what was
    # already there. resume: the plan covers the whole video and store.episodic ends up covering it
    # too, so len(plan) alone is right.
    before = len(backend.load(ns).episodic) if (existing and mode == "append") else 0

    t0 = time.time()
    store = None
    for status, _log, _view, st in pipeline.build_memory(
        video=str(video),
        window_sec=window,
        step_sec=step,
        max_clips=int(max_clips),
        rollup_k=rollup_k,
        do_consolidate=True,
        height=height,
        namespace=ns,
        overwrite=(mode == "rebuild"),
        clips_dir=str(clips_root),
        resume=(mode == "resume"),
    ):
        if st is not None:
            store = st
        if status:
            print(f"[BUILD] {status}", flush=True)

    if store is None:
        return {"namespace": ns, "status": "failed", "error": "pipeline produced no store"}

    got, want = len(store.episodic), before + len(plan)
    res = {
        "namespace": ns,
        "memory_dir": str(mdir),
        "episodic": got,
        "planned": want,
        "people": len(store.entities),
        "semantic_facts": len(store.semantic),
        "elapsed_min": round((time.time() - t0) / 60, 1),
        "status": "complete" if got >= want else "truncated",
    }
    if before:
        res["appended_to"] = f"{before} clips already in the timeline"
    if got < want:
        print(
            f"[BUILD] ⚠️ {ns}: TRUNCATED {got}/{want} clips — a clip exhausted its retries, the "
            f"loop broke, and the library was still finalized, so it LOOKS normal. Resume it:\n"
            f"         python3 {_HERE} {video} --mode resume",
            flush=True,
        )
    else:
        print(
            f"[BUILD] ✅ {ns}: {got} clips · {len(store.entities)} people · "
            f"{len(store.semantic)} facts · {res['elapsed_min']} min",
            flush=True,
        )
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Build omni-memory for one video or a directory")
    ap.add_argument("video", nargs="?", help="Path to the source video")
    ap.add_argument("--video-dir", help="Build every video in this directory")
    ap.add_argument(
        "--namespace",
        help="Shared-library memory name. Give the SAME name for several videos to stream them into "
        "one continuous memory (identities and facts carry across). Omit for a per-video "
        "memory at <video>.memory/.",
    )
    ap.add_argument(
        "--mode",
        choices=("new", "append", "resume", "rebuild"),
        default="new",
        help="new: fail if it exists · append: continue an existing timeline with this video "
        "(streaming) · resume: continue an interrupted build · "
        "rebuild: discard and start over",
    )
    ap.add_argument("--window", type=int, default=30, help="Window seconds")
    ap.add_argument("--step", type=int, default=25, help="Step seconds (window - step = overlap)")
    ap.add_argument("--height", type=int, default=480, help="Clip height")
    ap.add_argument("--rollup-k", type=int, default=10, help="Clips per stage-2 induction batch")
    ap.add_argument("--max-clips", type=int, default=0, help="0 = whole video; small value = smoke test")
    ap.add_argument(
        "-j", "--concurrency", type=int, default=1, help="Videos in parallel. Each video is serial internally."
    )
    ap.add_argument(
        "--model",
        default=omni_core.MODEL,
        help="Omni model for extraction, induction and name alignment. Defaults to "
        f"{omni_core.MODEL!r} ($QWEN_MM_API_OMNI_MODEL if set). All three stages use it.",
    )
    ap.add_argument("--log", help="Where to write the build log. Defaults to a file beside the memory.")
    a = ap.parse_args()
    omni_core.set_chat_model(a.model)

    if a.video_dir:
        # is_file() as well as the suffix: a directory or a dangling symlink named *.mp4 would
        # otherwise reach ffmpeg and take a whole batch down partway through.
        root = Path(a.video_dir).expanduser().resolve()
        vids = sorted(p for p in root.iterdir() if p.suffix.lower() in VIDEO_EXTS and p.is_file())
        if not vids:
            print(f"no videos under {a.video_dir}", file=sys.stderr)
            return 2
    elif a.video:
        v = Path(a.video).expanduser().resolve()
        if not v.is_file():
            print(f"video not found: {v}", file=sys.stderr)
            return 2
        vids = [v]
    else:
        ap.error("pass a VIDEO or --video-dir")
        return 2

    # STREAMING: one namespace + several videos = a single continuous memory. The segments MUST run
    # in order and serially — each one starts from the previous one's driver_state, so identities and
    # semantic facts carry across videos. Parallelism is impossible here by construction.
    streaming = bool(a.namespace) and len(vids) > 1
    if streaming and a.concurrency > 1:
        print(
            f"[BUILD] -j {a.concurrency} ignored: streaming {len(vids)} videos into '{a.namespace}' "
            f"is inherently serial (state threads from segment to segment).",
            flush=True,
        )
        a.concurrency = 1

    # Beside the memory it produces, so the log is findable later from the memory alone.
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if a.log:
        log_path = Path(a.log).expanduser().resolve()
    elif a.namespace:
        root = Path(config.local_dir()) if config.local_dir() else vids[0].parent
        log_path = root / a.namespace / f"build_{stamp}.log"
    elif a.video_dir:
        log_path = Path(a.video_dir).expanduser().resolve() / f"omni_memory_build_{stamp}.log"
    else:
        log_path = Path(str(vids[0]) + MEMORY_SUFFIX) / f"build_{stamp}.log"
    start_logging(log_path)

    print(
        f"=== omni-memory build: {len(vids)} video(s), {a.window}s/{a.step}s/{a.height}p, "
        f"mode={a.mode}, j={a.concurrency}" + (f", streaming → '{a.namespace}'" if streaming else "") + " ===",
        flush=True,
    )
    print(f"=== model: {omni_core.MODEL} @ {omni_core.BASE_URL} ===", flush=True)
    print(f"=== log: {log_path} ===", flush=True)
    if _ENV_TUNING:
        print(f"=== from the environment: {' '.join(f'{k}={v}' for k, v in _ENV_TUNING.items())} ===", flush=True)
    kw = dict(
        mode=a.mode,
        namespace=a.namespace,
        window=a.window,
        step=a.step,
        height=a.height,
        rollup_k=a.rollup_k,
        max_clips=a.max_clips,
    )

    results = []
    if streaming:
        for i, v in enumerate(vids):
            # First segment obeys --mode (new/rebuild); every later one appends to the timeline.
            seg_mode = a.mode if i == 0 else "append"
            print(f"\n[BUILD] ── segment {i + 1}/{len(vids)}: {v.name} (mode={seg_mode}) ──", flush=True)
            try:
                results.append(build_one(v, **{**kw, "mode": seg_mode}))
            except Exception as e:
                print(f"[BUILD] ❌ {v.name}: {type(e).__name__}: {e}", flush=True)
                results.append({"namespace": a.namespace, "status": "failed", "error": str(e)[:200]})
                print("[BUILD] stopping the stream — later segments depend on this one's state", flush=True)
                break
    elif len(vids) == 1 or a.concurrency <= 1:
        for v in vids:
            try:
                results.append(build_one(v, **kw))
            except Exception as e:
                print(f"[BUILD] ❌ {v.name}: {type(e).__name__}: {e}", flush=True)
                results.append({"namespace": v.stem, "status": "failed", "error": str(e)[:200]})
    else:
        with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            futs = {ex.submit(build_one, v, **kw): v for v in vids}
            for f in as_completed(futs):
                v = futs[f]
                try:
                    results.append(f.result())
                except Exception as e:
                    print(f"[BUILD] ❌ {v.name}: {type(e).__name__}: {e}", flush=True)
                    results.append({"namespace": v.stem, "status": "failed", "error": str(e)[:200]})

    bad = [r for r in results if r.get("status") in {"truncated", "failed"}]
    print("\n=== summary ===", flush=True)
    for r in sorted(results, key=lambda x: x.get("namespace", "")):
        print(
            f"  {r.get('status', '?'):10s} {r.get('namespace', '?'):24s} "
            f"{r.get('episodic', '?')}/{r.get('planned', '?')} clips",
            flush=True,
        )
    if bad:
        print(f"\n{len(bad)} of {len(results)} need attention (resume truncated ones with --mode resume)", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
