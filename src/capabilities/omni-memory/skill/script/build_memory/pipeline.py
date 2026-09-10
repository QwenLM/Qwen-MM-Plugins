"""build_memory — the write path: slice, extract each clip statefully, induce, index, save.

A generator that yields progress as it goes; driven by skill/script/build_memory/build_memory.py. Reading a
memory back does not come through here at all — that is service.py.
"""

import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import clipping
import omni_core
import stages
import storage
from store_writer import MemoryStore


def _fmt_state(s):
    """One-line snapshot of the driver state for the UI / log."""
    if not s:
        return "（无）"
    ents = (
        "，".join(f"{e['person_id']}" + (f"/{e['name']}" if e.get("name") else "") for e in s.get("known_entities", []))
        or "无"
    )
    return (
        f"scene={s.get('scene_id')} · 实体[{len(s.get('known_entities', []))}]：{ents} · "
        f"event={s.get('active_event_id')} · env累积={len(s.get('scene_env', []))} · "
        f"until={s.get('last_processed_until')}"
    )


def _run_hb(executor, fn, args, emit_fn, label, every=5):
    """Run a BLOCKING fn on a background thread while the generator keeps yielding heartbeats, so a
    long omni call or a rate-limit backoff never leaves the caller with minutes of silence. Returns
    fn's result (re-raises its exception). Drive it with `yield from`."""
    import concurrent.futures as _cf

    fut = executor.submit(fn, *args)
    t0 = time.time()
    while True:
        try:
            return fut.result(timeout=every)
        except _cf.TimeoutError:
            w = int(time.time() - t0)
            yield emit_fn(
                f"{label}… 已用 {w}s" + ("（模型调用/限流退避中，处理仍在进行，请勿刷新页面）" if w > 12 else "")
            )


def build_memory(
    video,
    window_sec,
    step_sec,
    max_clips,
    rollup_k,
    do_consolidate,
    height,
    namespace,
    overwrite=False,
    clips_dir=None,
    resume=False,
):
    """Own the scratch directory, and delete it however the build ends.

    A thin wrapper rather than a try/finally around the body below, which would re-indent four
    hundred lines to no other purpose. `finally` in a generator runs on exhaustion, on an early
    return, on an exception and on close(), so the one thing the directory cannot do is outlive the
    build. It used to: every run left a /tmp/ommem_* behind, empty on the pre-sliced path that
    build_memory.py always takes, and holding both a 480p and a full-resolution copy of every clip
    on the path that has no cache to reuse.

    Replay copies are read out of it by the storage backend's single worker; the final flush waits on
    a future queued behind them, so on a completed build they have all landed before this returns. An
    aborted build can lose one, which get_moment and the replay planner already tolerate — they check
    the file is there before offering it.
    """
    tmp_dir = tempfile.mkdtemp(prefix="ommem_")
    try:
        yield from _build_memory(
            video,
            window_sec,
            step_sec,
            max_clips,
            rollup_k,
            do_consolidate,
            height,
            namespace,
            overwrite=overwrite,
            clips_dir=clips_dir,
            resume=resume,
            tmp_dir=tmp_dir,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_memory(
    video,
    window_sec,
    step_sec,
    max_clips,
    rollup_k,
    do_consolidate,
    height,
    namespace,
    overwrite=False,
    clips_dir=None,
    resume=False,
    tmp_dir=None,
):
    """Generator: sliding clips → sequential stateful extract → stage-2 → index → save.
    Yields (status, build_log, mem_view, state).

    Three ways to meet an existing library:
      · overwrite=True  → discard it and rebuild from scratch
      · resume=True     → SAME video, continue after the clips already ingested (crash recovery).
                          Timeline is unchanged: offset/idx_offset stay 0 and the plan is trimmed.
      · otherwise       → APPEND: a DIFFERENT video is stitched onto the end of the timeline, its
                          timestamps shifted by store.time_offset().

    Long, and deliberately still one function: the phases share about ten pieces of mutable state
    (the store, the driver state, the rollup futures, the pending name-alignment future, the batch
    cursor, the accumulated renames) and every one of them yields progress, so splitting them into
    closures would trade the length for a set of `nonlocal` declarations that nothing tests.

    The body is marked with `# ---- … ----` banners, in this order:

      · resolve the namespace, then overwrite / resume / append against what is already there
      · slice the video into windows and write the clip plan
      · pipelined ingestion: serial stateful extract || background incremental stage-2
          · streaming name-alignment (L2) cadence + state
      · finalize name-alignment step A — full-transcript pass, before driver_state is frozen
      · finalize stage-2 — flush the trailing batch, drain the rollups, consolidate
      · finalize name-alignment step B — propagate renames into semantic/episodic text
      · dense index over episodic records and semantic triples
      · final flush
      · timing summary
    """
    log = []
    backend = storage.get_backend()

    def emit(status):
        # Slots 2 and 3 are legacy placeholders; callers read only `status` and `store`.
        return status, "\n\n---\n\n".join(log), "", store

    if not video:
        yield "⚠️ 请先上传视频。", "", "", None
        return

    try:
        client = omni_core.get_client()
    except Exception as e:
        yield f"⚠️ 无法创建模型客户端（检查配置/API_KEY）：{str(e)[:200]}", "", "", None
        return

    # ---- resolve the namespace, then overwrite / resume / append against what is already there ----
    # namespace decision: an EXISTING library is resumed (same video) or appended to (next video);
    # overwrite rebuilds from scratch.
    ns = (namespace or "").strip() or time.strftime("run-%Y%m%d-%H%M%S")
    oss_on = backend.available()
    existing = oss_on and ns in backend.list_namespaces()
    resuming = bool(resume) and existing and not overwrite
    append = existing and not overwrite and not resuming
    if append or resuming:
        try:
            store = backend.load(ns)
        except Exception as e:
            yield f"⚠️ 无法加载记忆库「{ns}」：{str(e)[:160]}", "", "", None
            return
    else:
        store = MemoryStore()
    # resume stays on the SAME timeline, so nothing is shifted; append starts a new segment.
    offset = store.time_offset() if append else 0.0
    idx_offset = store.clip_idx_next() if append else 0
    if resuming:
        # State comes from the LAST INGESTED CLIP, not from store.driver_state: driver_state is only
        # assigned during finalize, and an interrupted build reaches finalize via `break` before that
        # — so in a truncated library it is None. Using it would silently restart from a blank state
        # and re-number every person_id. Each episodic record carries its own state_after.
        _sa = (store.episodic[-1].get("state_after") if store.episodic else None) or None
        state0 = json.loads(json.dumps(_sa)) if _sa else stages.new_state()
        log.append(
            f"⏵ **续跑模式**：接上「{ns}」的第 {len(store.episodic)} 片"
            + (
                f"（承接状态：{len(state0.get('known_entities') or [])} 实体 · "
                f"scene={state0.get('scene_id')} · until={state0.get('last_processed_until')}）"
                if _sa
                else "（⚠️ 末片无 state_after，只能从空状态继续，身份可能断裂）"
            )
            + "。时间轴不变。"
        )
    elif append:
        state0 = json.loads(json.dumps(store.driver_state)) if store.driver_state else stages.new_state()
        state0["last_processed_until"] = None  # new segment: model view starts at 0
        log.append(
            f"➕ **追加模式**：接续「{ns}」（已处理 {offset / 60:.1f} 分钟 / {len(store.episodic)} 片 / "
            f"{len(store.entities)} 实体）→ 本段时间轴从 {int(offset // 60):02d}:{int(offset % 60):02d} 起。"
        )
    else:
        state0 = stages.new_state()
        log.append(f"🆕 覆盖重建「{ns}」。" if (existing and overwrite) else f"🆕 新建记忆库「{ns}」。")

    # ---- slice the video into windows and write the clip plan ----
    window_sec, step_sec = int(window_sec), min(int(step_sec), int(window_sec))
    yield "✂️ 正在探测时长 / 规划滑动窗口…", "", "", store
    t_all0 = time.time()
    cached = None
    if clips_dir:
        _vstem = os.path.splitext(os.path.basename(video))[0]
        _pp = os.path.join(clips_dir, _vstem, "plan.json")
        if os.path.exists(_pp):
            try:
                with open(_pp, encoding="utf-8") as f:
                    cached = json.load(f)
            except (OSError, ValueError):
                # Everything an unreadable cache raises and nothing else: OSError from the read,
                # ValueError covering both JSONDecodeError and the UnicodeDecodeError a half-written
                # file leaves. Missing the latter — as narrowing to JSONDecodeError alone would —
                # turns a re-sliceable cache into a crash.
                cached = None
    try:
        if cached is not None:
            dur = max((c["win_end"] for c in cached), default=0.0)
            plan = [
                {
                    "idx": c["idx"],
                    "win_start": c["win_start"],
                    "win_end": c["win_end"],
                    "overlap": c.get("overlap", window_sec - step_sec),
                    "_cached_path": c["path"],
                }
                for c in cached
            ]
            log.append(f"⚡ 预切缓存命中（{clips_dir}/{_vstem}）：{len(plan)} 片，跳过切片。")
        else:
            dur, plan = clipping.plan_clips(video, window=window_sec, step=step_sec, max_clips=int(max_clips))
    except Exception as e:
        yield f"⚠️ 规划切片失败：{str(e)[:300]}", "", "", None
        return
    if not plan:
        yield "⚠️ 无法规划切片（视频为空/无法读取时长？）。", "", "", None
        return

    if resuming:
        # Skip what is already ingested FOR THIS VIDEO. len(store.episodic) is the whole-library
        # total, which differs once other videos have been appended into the same memory — comparing
        # that against this video's plan would report "already complete" and skip everything. The
        # plan carries global idx values, so intersecting it with the idx values already in the store
        # gives the right count regardless of how many videos share the memory.
        _have = {o.get("idx") for o in store.episodic}
        _todo = [w for w in plan if w["idx"] not in _have]
        if not _todo:
            log.append(f"⏵ 「{ns}」本视频已完整（{len(plan)} 片），无需续跑。")
            yield emit(f"✅ 已完整：{len(plan)} 片")
            return
        _done = len(plan) - len(_todo)
        plan = _todo
        log.append(f"⏵ 跳过已建的 {_done} 片，本次续跑 **{len(plan)}** 片（{plan[0]['win_start']}s 起）。")

    log.append(
        f"✂️ 视频约 {dur:.0f}s → 规划 **{len(plan)}** 片"
        f"（{window_sec}s 窗 / {step_sec}s 步，重编码 {int(height)}p）。逐片切片中……"
    )
    yield emit(f"✂️ 切片 0/{len(plan)}")
    t_c0 = time.time()
    clips = []
    for _n, w in enumerate(plan, 1):
        yield emit(f"✂️ 切片 {_n}/{len(plan)}（{w['win_start']}-{w['win_end']}s）…")
        try:
            if w.get("_cached_path"):
                c = {
                    "idx": w["idx"],
                    "path": w["_cached_path"],
                    "win_start": w["win_start"],
                    "win_end": w["win_end"],
                    "overlap": w["overlap"],
                }  # cache hit
            else:
                c = clipping.cut_window(video, tmp_dir, w, height=int(height))  # low-res for omni
        except Exception as e:
            log.append(f"⚠️ 第 {w['idx']} 片切片失败：{str(e)[:200]}")
            yield emit(f"⚠️ 第 {w['idx']} 片切片失败，停止。")
            return
        if oss_on:  # store replay clip under its GLOBAL index (append must not overwrite earlier clips)
            gidx = idx_offset + w["idx"]
            try:
                # A cached clip already lives somewhere permanent, so replay points straight at it
                # instead of copying a second copy into the memory.
                if w.get("_cached_path"):
                    c["oss_key"] = os.path.abspath(c["path"])
                else:  # no cache — keep a full-resolution copy so the memory is self-contained
                    replay_path = clipping.cut_window_original(video, tmp_dir, w)
                    backend.upload_clip_async(ns, gidx, replay_path)
                    c["oss_key"] = backend.clip_key(ns, gidx)
            except Exception as e:
                log.append(f"（片 {gidx} replay clip 存储跳过：{str(e)[:80]}）")
        clips.append(c)
    t_clip = round(time.time() - t_c0, 1)
    # ⑤ Native pointers under GLOBAL index + global time (append keeps earlier clips)
    gclips = [
        {
            "idx": idx_offset + c["idx"],
            "path": c.get("path"),
            "win_start": round(c["win_start"] + offset, 3),
            "win_end": round(c["win_end"] + offset, 3),
            "oss_key": c.get("oss_key"),
        }
        for c in clips
    ]
    if resuming:
        # store.clips already holds pointers for the WHOLE video (they are written up-front at slice
        # time), while gclips only covers the clips this run touched. Merge by idx so the earlier
        # pointers survive — set_clips would drop them and break replay for everything before the
        # resume point, add_clips would duplicate them.
        _by_idx = {c["idx"]: c for c in store.clips}
        _by_idx.update({c["idx"]: c for c in gclips})
        store.set_clips([_by_idx[k] for k in sorted(_by_idx)])
    elif append:
        store.add_clips(gclips)
    else:
        store.set_clips(gclips)
    log.append(
        f"✂️ 切片完成：**{len(clips)}** 片（{t_clip}s"
        + ("，原分辨率已存供 replay" if oss_on else "")
        + "）。逐片**有状态**抽取……"
    )
    yield emit(f"🧠 有状态建记忆 0/{len(clips)}")

    # ---- pipelined ingestion: serial stateful extract  ||  background incremental stage-2 ----
    state = state0
    t_e0 = time.time()
    omni_core.diag(f"[MEM] === EXTRACT phase start: {len(clips)} clips ===", flush=True)
    # stage-2 store: resume and append both continue CRUD on the triples already induced rather than
    # starting over. `emb` is dropped on the way in because stage2_finalize serializes this store into
    # its merge prompt, where a vector is ~22 KB of decimal text the model was never asked to read
    # (measured across 10 libraries: 813 KB → 13 KB). Over the context limit the call raises and the
    # caller keeps the unmerged triples, silently skipping the final dedup. Nothing is lost: build_index
    # re-embeds every semantic triple at the end of the run. Dropping it is also required, not just
    # cheaper — an in-memory vector is an ndarray, which the json deep-copy below cannot serialize.
    sem_store = (
        {
            t["key"]: json.loads(json.dumps({k: v for k, v in t.items() if k != "emb"}))
            for t in store.semantic
            if t.get("key")
        }
        if (append or resuming)
        else {}
    )
    sem_superseded = []  # owned by the single-worker stage-2 thread
    s2_agg = {"create": 0, "update": 0, "conflict": 0, "tokens": 0, "calls": 0, "elapsed": 0.0}
    s2_futs = []
    s2_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="s2") if do_consolidate else None
    hb_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hb")  # runs blocking calls off the yield-loop
    K = max(1, int(rollup_k))
    next_start = len(store.episodic)  # only roll up NEW clips (skip already-ingested)
    # ---- streaming name-alignment (L2) cadence + state ----
    NAME_ALIGN_EVERY = max(K, int(os.environ.get("MEM_NAME_ALIGN_EVERY", K)))  # default = K (every rollup cycle)
    name_fut = None  # pending background infer_roster future
    last_align_at = len(store.episodic)  # #clips at last L2 trigger
    applied_changes = []  # cumulative renames (for finalize semantic propagate)

    def _rollup(batch, ents_snap, tag):
        _t = time.time()
        ops = stages.stage2_rollup(client, ents_snap, batch, sem_store, sem_superseded)
        for kk in ("create", "update", "conflict"):
            s2_agg[kk] += ops[kk]
        s2_agg["tokens"] += ops["tokens"]
        s2_agg["calls"] += ops["calls"]
        s2_agg["elapsed"] += round(time.time() - _t, 2)
        active = [v for v in sem_store.values() if v.get("status") != "superseded"]
        store.set_semantic(json.loads(json.dumps(active)))  # deep-copy snapshot: safe vs main-thread serialize
        try:
            stages.gs_add_segment(client, store, batch)  # ① hierarchical global state (bounded, background)
        except Exception as e:
            omni_core.diag(f"[MEM] 🌐 global-state 段归纳跳过：{str(e)[:80]}", flush=True)
        msg = (
            f"🔗 [后台并行] rollup {tag}: +{ops['create']} 新 / {ops['update']} 更新 / "
            f"{ops['conflict']} 冲突 → 活跃 {len(active)}（{ops['tokens'] // 1000}k tok）"
        )
        omni_core.diag(f"[MEM] {msg}", flush=True)
        log.append(msg)

    for c in clips:
        rel_i = c["idx"]  # index within THIS segment (drives overlap/first)
        i = idx_offset + rel_i  # GLOBAL index (storage / display)
        # The pending name-alignment future is drained at the rollup boundary below.
        yield emit(
            f"🧠 正在处理片段 {i + 1}（本段 {rel_i + 1}/{len(clips)}，t≈{round(c['win_start'] + offset)}s，串行有状态）…"
        )
        # extract_clip builds its own previous_state via prev_state_for_prompt(state), without
        # global_context — the rolling global summary never enters the extraction prompt (§8.1).
        omni_core.diag(f"[MEM] EXTRACT global#{i} (seg {rel_i + 1}/{len(clips)}) start (omni video call)…", flush=True)
        try:
            rec, _ = yield from _run_hb(
                hb_exec,
                stages.extract_clip,
                (client, c, state, (rel_i == 0)),
                emit,
                f"🧠 处理片段 {i + 1}（本段 {rel_i + 1}/{len(clips)}）",
            )
        except Exception as e:
            log.append(f"### 🎞 片段 {i} ⚠️ 异常：{str(e)[:160]}")
            yield emit(f"🧠 片段 {i + 1} 异常，停止（有状态需顺序）")
            break
        if rec.get("error"):
            yield emit(f"🧠 片段 {i + 1} 抽取失败，停止（有状态需顺序）")
            break
        omni_core.diag(
            f"[MEM] EXTRACT global#{i} done in {rec.get('elapsed')}s "
            f"tok={(rec.get('usage') or {}).get('total_tokens', 0)} attempts={rec.get('attempts')}",
            flush=True,
        )
        stages.shift_record_time(rec, offset)  # segment-local times → GLOBAL continuous timeline
        rec["idx"] = i
        store.add_episodic(rec)
        stages.ledger_accumulate(store.name_ledger, stages.rows_from_records([rec]))  # L1: 0-LLM
        # L1-bis: fold name_candidates from anonymous entity mode into the ledger
        _ncands = (rec.get("assignments") or {}).get("name_candidates") or []
        if _ncands:
            stages.ledger_accumulate_candidates(store.name_ledger, _ncands)
        store.scene_env = list(state.get("scene_env", []))
        store.set_entities(state.get("known_entities", []))
        if oss_on:
            backend.save_async(ns, store)  # incremental snapshot (now also carries latest semantic)
        # every K clips → fire an incremental rollup that runs IN PARALLEL with the next extraction
        # Name alignment runs in background and is drained before the rollup snapshot is taken.
        if s2_exec and (len(store.episodic) - next_start) >= K:
            # ---- drain any pending transcript align result BEFORE taking ents_snap ----
            if name_fut is not None:
                try:
                    _roster = name_fut.result(timeout=120)
                    _nch = stages.apply_roster(
                        store, _roster, respect_lock=False, allow_override=True, propagate="entities"
                    )
                    omni_core.diag(
                        f"[MEM] 🪪 [transcript] roster applied: {len(_roster)} entries, {len(_nch)} changes", flush=True
                    )
                    if _nch:
                        _net = {}
                        for _ch in _nch:
                            if _ch["pid"] not in _net:
                                _net[_ch["pid"]] = {"pid": _ch["pid"], "old": _ch["old"], "new": _ch["new"]}
                            else:
                                _net[_ch["pid"]]["new"] = _ch["new"]
                        stages.propagate_names(store, list(_net.values()))
                        applied_changes += _nch
                        log.append("🪪 [transcript] " + "；".join(f"{x['pid']}:{x['old']}→{x['new']}" for x in _nch))
                except Exception as e:
                    log.append(f"🪪 [transcript] apply 跳过：{str(e)[:80]}")
                name_fut = None

            batch = list(store.episodic[next_start : next_start + K])  # frozen slice
            ents_snap = json.loads(json.dumps(store.entities))  # deep copy (entities mutate)
            s2_futs.append(s2_exec.submit(_rollup, batch, ents_snap, f"clips[{batch[0]['idx']}..{batch[-1]['idx']}]"))
            next_start += K

            # ---- fire next transcript align in background (runs parallel with extraction) ----
            if name_fut is None and (len(store.episodic) - last_align_at) >= NAME_ALIGN_EVERY:
                last_align_at = len(store.episodic)
                try:
                    _aprompt = stages.build_transcript_align_prompt(store, max_lines=500)
                    name_fut = hb_exec.submit(stages.infer_roster, client, _aprompt)
                    omni_core.diag(
                        f"[MEM] 🪪 [transcript] align submitted at clip {len(store.episodic)} (background)", flush=True
                    )
                except Exception as e:
                    log.append(f"🪪 [transcript] 提交跳过：{str(e)[:80]}")
        yield emit(
            f"🧠 已建 {i + 1}/{len(clips)} 片 · {_fmt_state(rec.get('state_after'))}"
            + ("　💾已增量存" if oss_on else "")
            + (f"　🔗后台归纳×{len(s2_futs)}" if s2_exec else "")
        )
        if c is not clips[-1]:  # smooth request rate; back off more if this clip hit throttling
            time.sleep(clipping.INTER_CLIP_SLEEP * (3 if (rec.get("attempts") or 1) > 1 else 1))

    t_ext_wall = round(time.time() - t_e0, 1)
    omni_core.diag(f"[MEM] === EXTRACT phase done: {len(store.episodic)} clips in {t_ext_wall}s ===", flush=True)
    if not store.episodic:
        if s2_exec:
            s2_exec.shutdown(wait=False)
        hb_exec.shutdown(wait=False)
        yield emit("⚠️ 没有成功抽取任何片段（可能限流/切片问题），请重试。")
        return

    store.scene_env = list(state.get("scene_env", []))  # ① global_summary managed by gs (flushed below)
    store.set_entities(state.get("known_entities", []))
    # ---- finalize name-alignment step A: one final pass over the FULL cumulative transcript, BEFORE
    # driver_state is frozen (so the persisted state carries corrected names for the next append). ----
    if s2_exec:
        # drain any pending background transcript align first
        if name_fut is not None:
            try:
                _roster = name_fut.result(timeout=120)
                _nch = stages.apply_roster(
                    store, _roster, respect_lock=False, allow_override=True, propagate="entities"
                )
                if _nch:
                    applied_changes += _nch
                    omni_core.diag(f"[MEM] 🪪 [transcript] finalize drain: {len(_nch)} changes", flush=True)
            except Exception as e:
                log.append(f"🪪 [transcript] finalize drain 跳过：{str(e)[:80]}")
            name_fut = None
        # one final full-video transcript alignment (override; propagation happens in step B)
        try:
            _ap = stages.build_transcript_align_prompt(store, max_lines=500)
            _roster = yield from _run_hb(
                hb_exec, stages.infer_roster, (client, _ap), emit, "🪪 [transcript] 人名推理（末尾兜底）"
            )
            _fch = stages.apply_roster(store, _roster, respect_lock=False, allow_override=True, propagate="entities")
            applied_changes += _fch
            if _fch:
                log.append("🪪 [transcript·兜底] " + "；".join(f"{x['pid']}:{x['old']}→{x['new']}" for x in _fch))
        except Exception as e:
            log.append(f"🪪 [transcript] 兜底跳过：{str(e)[:100]}")
    store.driver_state = json.loads(json.dumps(state))  # persist final state for the NEXT segment
    store.processed_sec = round(store.episodic[-1].get("win_end", offset), 3) if store.episodic else offset
    log.append(
        f"🗂 容器就绪：🌐Global「{(store.global_summary or '')[:50]}」·"
        f"🔑{len(store.entities)} 实体（keys 常驻）·🎞{len(store.episodic)} 复合片段"
        f"（时间轴 0–{store.processed_sec / 60:.1f} 分钟）"
    )

    # ---- finalize stage-2: flush trailing batch, wait all background rollups, consolidate ----
    s2_stats = None
    if s2_exec:
        if (len(store.episodic) - next_start) > 0:  # trailing partial batch
            batch = list(store.episodic[next_start:])
            ents_snap = json.loads(json.dumps(store.entities))
            s2_futs.append(s2_exec.submit(_rollup, batch, ents_snap, f"clips[{batch[0]['idx']}..{batch[-1]['idx']}]"))
        yield emit(f"🔗 等待 {len(s2_futs)} 个后台归纳完成…")
        import concurrent.futures as _cf

        for fi, f in enumerate(s2_futs):
            while True:
                try:
                    f.result(timeout=5)
                    break
                except _cf.TimeoutError:
                    yield emit(f"🔗 等待后台归纳完成…（{fi}/{len(s2_futs)}）")
                except Exception as e:
                    log.append(f"🔗 rollup 异常：{str(e)[:150]}")
                    break
        s2_exec.shutdown(wait=True)
        try:
            consolidated, ftok = yield from _run_hb(
                hb_exec, stages.stage2_finalize, (client, sem_store), emit, "🔗 末尾整合（去重合并）"
            )
            s2_agg["tokens"] += ftok
            s2_agg["calls"] += 1
            store.set_semantic(consolidated)
            s2_stats = dict(s2_agg, active=len(consolidated), superseded=len(sem_superseded))
            log.append(
                f"✅ 归纳完成：🧩 **{len(consolidated)}** 条三元组"
                f"（新增/更新/冲突 = {s2_agg['create']}/{s2_agg['update']}/{s2_agg['conflict']}，"
                f"软删 {len(sem_superseded)}，{s2_agg['tokens'] // 1000}k tok，"
                f"归纳 {round(s2_agg['elapsed'], 1)}s 与抽取并行）"
            )
        except Exception as e:
            store.set_semantic([v for v in sem_store.values() if v.get("status") != "superseded"])
            s2_stats = dict(s2_agg, active=len(store.semantic), superseded=len(sem_superseded))
            log.append(f"🔗 末尾整合失败（保留已归纳 {len(store.semantic)} 条）：{str(e)[:150]}")

    # ---- finalize name-alignment step B: propagate confirmed renames into semantic/episodic text
    # so retrieved facts & captions read the corrected name (entities already fixed in step A). Runs
    # before build_index → embeddings use corrected text. One-time (bounded per segment). ----
    if applied_changes:
        _net = {}
        for _ch in applied_changes:
            if _ch["pid"] not in _net:
                _net[_ch["pid"]] = {"pid": _ch["pid"], "old": _ch["old"], "new": _ch["new"]}
            else:
                _net[_ch["pid"]]["new"] = _ch["new"]
        try:
            stages.propagate_names(store, list(_net.values()))
            log.append(f"🪪 人名传播到 semantic/episodic：{len(_net)} 人")
        except Exception as e:
            log.append(f"🪪 人名传播跳过：{str(e)[:80]}")

    stages.gs_flush(store)  # refresh global_summary from the final nodes; no model call

    # ---- dense index over episodic records and semantic triples ----
    t_i0 = time.time()
    t_idx = 0.0
    try:
        # Named rather than hard-coded: EMBED_MODEL_NAME can change it, and a progress line that
        # reports the wrong model is worse than one that reports none.
        ok = yield from _run_hb(
            hb_exec, store.build_index, (client,), emit, f"🔢 建立向量索引（{omni_core.EMBED_MODEL}）"
        )
        t_idx = round(time.time() - t_i0, 1)
        log.append(
            f"🔢 向量索引已建（{t_idx}s，混合检索：向量 + 关键词 RRF）。" if ok else "🔢 向量不可用，退回关键词检索。"
        )
    except Exception as e:
        t_idx = round(time.time() - t_i0, 1)
        log.append(f"🔢 向量索引失败（退回关键词）：{str(e)[:120]}")
    hb_exec.shutdown(wait=False)

    # ---- final flush (complete snapshot incl. semantic + vectors; last write wins) ----
    t_oss = 0.0
    if oss_on:
        t_o0 = time.time()
        try:
            fut = backend.save_async(ns, store)  # queued after all incremental saves (single worker)
            if fut:
                fut.result(timeout=120)  # wait for the final, most-complete snapshot
            t_oss = round(time.time() - t_o0, 1)
            log.append(f"💾 已持久化到记忆库 **{ns}**（增量+异步，最终 flush {t_oss}s；重启后可加载/切换）。")
        except Exception as e:
            t_oss = round(time.time() - t_o0, 1)
            log.append(f"💾 最终保存失败（记忆仍在会话内）：{str(e)[:160]}")
    else:
        log.append(f"💾 {backend.status_line()}")

    # ---- timing summary (spot the bottleneck) ----
    ext_model = round(sum(r.get("elapsed", 0) or 0 for r in store.episodic), 1)
    n_ext = len(store.episodic)
    ext_tok = sum((r.get("usage") or {}).get("total_tokens", 0) for r in store.episodic)
    s2_el = (s2_stats or {}).get("elapsed", 0)
    s2_tok = (s2_stats or {}).get("tokens", 0)
    t_all = round(time.time() - t_all0, 1)
    log.append(
        f"⏱ **总 {t_all}s** ｜ 切片 {t_clip}s · 抽取 {ext_model}s"
        f"（均 {round(ext_model / max(n_ext, 1), 1)}s/片，墙钟含增量存 {t_ext_wall}s）· "
        f"归纳 {s2_el}s(与抽取并行) · 索引 {t_idx}s · 存储 {t_oss}s ｜ "
        f"~{round((ext_tok + s2_tok) / 1000)}k tok（抽取 {round(ext_tok / 1000)}k + 归纳 {round(s2_tok / 1000)}k）"
    )
    omni_core.diag(
        f"[TIME] total={t_all}s clip={t_clip}s extract_model={ext_model}s extract_wall={t_ext_wall}s "
        f"stage2={s2_el}s index={t_idx}s save={t_oss}s tokens={ext_tok + s2_tok} "
        f"(extract={ext_tok} stage2={s2_tok})",
        flush=True,
    )

    yield emit(
        f"✅ 记忆就绪：🎞{len(store.episodic)} 复合片段 · 🔑{len(store.entities)} 常驻实体 · "
        f"🧩{len(store.semantic)} 三元组。右侧可提问了。"
    )


def _fmt_retry(events):
    """One-line backoff summary for the retrieval panel (per stage)."""
    if not events:
        return ""
    tot = round(sum(e.get("wait", 0) for e in events), 1)
    detail = ", ".join(f"#{e['attempt']}·{e['reason']}·{e['wait']}s" for e in events)
    return f"  ⏳ **退避 {len(events)} 次 / 共 {tot}s**（{detail}）"


def _retry_sum(events):
    """Compact backoff summary for stdout [TIME] logs."""
    if not events:
        return "0"
    return f"{len(events)}x/{round(sum(e.get('wait', 0) for e in events), 1)}s"
