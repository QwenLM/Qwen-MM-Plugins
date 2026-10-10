"""Phase 2 pipeline worker: polls 01_macros.json and extracts subgraphs concurrently."""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from build_graph import (
    MacroEvent,
    _extract_one_subgraph,
    _save_subgraph_compat,
)
from env_config import ConfigurationError, get_env


def _read_macros_from_checkpoint(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _p1_alive(pid: int | None) -> bool:
    if pid is None:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def run_pipeline_worker(
    video_path: str,
    output_dir: str,
    model: str | None,
    api_key: str,
    num_workers: int = 10,
    poll_interval: float = 2.0,
    p1_pid: int | None = None,
):
    macros_path = os.path.join(output_dir, "01_macros.json")
    done_marker = os.path.join(output_dir, "01_done")
    subgraph_dir = os.path.join(output_dir, "subgraphs")
    os.makedirs(subgraph_dir, exist_ok=True)

    submitted: set[str] = set()
    for f in os.listdir(subgraph_dir):
        if f.endswith(".json"):
            submitted.add(f[:-5])
    if submitted:
        print(f"[P2-WORKER] Resuming: {len(submitted)} pre-built subgraphs found")

    executor = ThreadPoolExecutor(max_workers=num_workers)
    futures: dict = {}  # future -> macro_id
    completed_count = 0
    failed_ids: list[str] = []
    p1_failed = False

    def _submit_new_macros():
        raw = _read_macros_from_checkpoint(macros_path)
        new_count = 0
        for m in raw:
            mid = m.get("macro_id", "")
            if not mid or mid in submitted:
                continue
            submitted.add(mid)
            macro = MacroEvent(
                macro_id=mid,
                label=m.get("label", ""),
                time_range=m.get("time_range", [0, 0]),
                summary=m.get("summary", ""),
                asr_text=m.get("asr_text", ""),
            )
            idx = int(mid.split("_")[1])
            args = (idx, macro, video_path, model, api_key)
            fut = executor.submit(_extract_one_subgraph, args)
            futures[fut] = (mid, macro)
            new_count += 1
        if new_count:
            print(f"[P2-WORKER] Submitted {new_count} new macros (total submitted: {len(submitted)})")

    def _collect():
        nonlocal completed_count
        done_futs = [f for f in futures if f.done()]
        for fut in done_futs:
            mid, macro_template = futures.pop(fut)
            try:
                _, updated = fut.result()
                _save_subgraph_compat(output_dir, updated)
                n_ent = len(updated.subgraph.entities) if updated.subgraph else 0
                n_ev = len(updated.subgraph.micro_events) if updated.subgraph else 0
                completed_count += 1
                print(f"[P2-WORKER] {mid}: done ({n_ent} entities, {n_ev} events) [{completed_count}/{len(submitted)}]")
            except ConfigurationError:
                raise
            except Exception as e:
                completed_count += 1
                failed_ids.append(mid)
                print(f"[P2-WORKER] {mid}: ERROR {e}")

    print(f"[P2-WORKER] Started ({num_workers} workers, polling every {poll_interval}s)")
    print(f"[P2-WORKER] Monitoring: {macros_path}")

    while True:
        _submit_new_macros()
        _collect()

        p1_done = os.path.exists(done_marker)
        if p1_done:
            _submit_new_macros()
            break

        if not _p1_alive(p1_pid) and not os.path.exists(done_marker):
            print("[P2-WORKER] WARNING: P1 process exited without 01_done, draining remaining macros")
            p1_failed = True
            _submit_new_macros()
            break

        time.sleep(poll_interval)

    # Wait for all in-flight workers
    if futures:
        print(f"[P2-WORKER] Waiting for {len(futures)} in-flight workers...")
        for fut in as_completed(futures):
            mid, macro_template = futures.pop(fut) if fut in futures else ("?", None)
            try:
                _, updated = fut.result()
                _save_subgraph_compat(output_dir, updated)
                completed_count += 1
                print(f"[P2-WORKER] {mid}: done [{completed_count}/{len(submitted)}]")
            except ConfigurationError:
                raise
            except Exception as e:
                completed_count += 1
                failed_ids.append(mid)
                print(f"[P2-WORKER] {mid}: ERROR {e}")

    executor.shutdown(wait=True)

    if failed_ids:
        print(f"[P2-WORKER] WARNING: {len(failed_ids)} macros failed: {failed_ids}")

    if p1_failed:
        raise RuntimeError("Phase 1 exited without 01_done")

    Path(os.path.join(output_dir, "02_done")).touch()
    print(f"[P2-WORKER] Complete: {completed_count} macros processed, {len(failed_ids)} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 2 pipeline worker")
    parser.add_argument("video_path", help="Path to video file")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", default=None, help="VL model; defaults to QWEN_MM_API_VL_MODEL")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--num-workers", type=int, default=10)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--p1-pid", type=int, default=None)
    args = parser.parse_args()

    api_key = args.api_key or get_env("DASHSCOPE_API_KEY") or ""

    run_pipeline_worker(
        video_path=args.video_path,
        output_dir=args.output_dir,
        model=args.model,
        api_key=api_key,
        num_workers=args.num_workers,
        poll_interval=args.poll_interval,
        p1_pid=args.p1_pid,
    )
