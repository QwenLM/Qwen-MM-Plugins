"""Answering straight off video: sizing a payload, re-encoding, and the two calls that watch.

  probe_media / plan_watch_encode / transcode_whole   decide and produce the copy that gets sent
  watch_answer                                       one whole short video in a single request
  replay_answer_stream                               re-watch stored clips when the text cannot settle it

None of this reads a memory. WatchError carries the `kind` that decides whether the caller should fall
back to building one.
"""

import os
import subprocess
import time

from shared.syscmd import find_tool
from shared.video import probe_media as ffprobe_json

from . import prompts
from .omni_core import (
    MODEL,
    TEMP_KWARGS,
    backoff,
    diag,
    env_float,
    env_int,
    http_status,
    is_rate_limit,
    iter_deadline,
    retry_reset,
    sleep_note,
)

REPLAY_N = env_int("MEM_REPLAY_N", 3)  # how many clips a replay re-watches

# watch_and_answer re-encodes the whole video to this profile before sending it, so the payload scales
# with duration instead of the source's arbitrary bitrate. Same values _clip_one uses, so a direct
# watch and a built memory show the model the same picture.
WATCH_ENCODE_TIMEOUT = env_int("MEM_WATCH_ENCODE_TIMEOUT", 1800)

# What the encode AIMS at, in base64 MiB — a bitrate target, not a limit anyone enforces. Whether a
# request is too large is the endpoint's call, which it makes plainly ("Exceeded limit on max bytes per
# data-uri item : 20971520"); that classifies as `reject` and answers with the build fallback. A target
# is still needed because one fixed bitrate cannot serve both a 2-minute and a 20-minute video, and can
# exceed the source's own bitrate — growing the file while double-compressing it.
WATCH_MAX_B64_MB = env_float("MEM_WATCH_MAX_B64_MB", 19.0)

# 32 kbps mono AAC. Speech stays intelligible well below this, and stereo buys nothing here — speaker
# attribution comes from lip movement, not channel separation.
WATCH_ABITRATE_K = env_int("MEM_WATCH_ABITRATE_K", 32)

# Rungs tried highest-first: (height, minimum video kbps that is still worth looking at at that
# height). A rung is taken when the budget affords its floor for this video's duration; dropping
# resolution is what buys duration. Below 288p the picture stops carrying answers, so there is no lower
# rung and the caller is told to build instead.
WATCH_LADDER = ((480, 200), (360, 120), (288, 80))

# The routing bands, as minutes. Under PREFER, watching is the cheaper answer and no memory is needed;
# over MAX it is not an option at all, since one request cannot hold that much video. Between the two
# it depends on what the caller wants, so the decision belongs to the agent. Policy constants rather
# than derived from the payload budget, so the documented bands and the code cannot drift apart.
WATCH_PREFER_MIN = env_float("MEM_WATCH_PREFER_MIN", 10.0)

WATCH_MAX_MIN = env_float("MEM_WATCH_MAX_MIN", 30.0)

REPLAY_ANSWER_PROMPT = prompts.REPLAY_ANSWER_PROMPT

WATCH_ANSWER_PROMPT = prompts.WATCH_ANSWER_PROMPT


def _is_stall(msg):
    """Timed out or stalled mid-stream, as opposed to being refused. Covers iter_deadline's stall
    TimeoutError and the SDK's own read/connect timeouts."""
    m = str(msg).lower()
    return any(k in m for k in ("timeout", "timed out", "stalled", "read operation", "connection reset"))


def _is_misconfigured(msg):
    """The endpoint or the credentials are wrong, rather than this particular request being too big.

    Separated because the remedy for a refused request — build a memory — runs against the same
    endpoint with the same key, so here it would fail the same way after tens of minutes. The common
    case is 404 model_not_found: a typo in --model should not send anyone off to build.
    """
    m = str(msg).lower()
    if any(http_status(m, c) for c in ("401", "403", "404")):
        return True
    return any(
        k in m
        for k in (
            "model_not_found",
            "does not exist or you do not have access",
            "invalid_api_key",
            "incorrect api key",
            "authentication",
            "unauthorized",
            "permission",
            "access denied",
        )
    )


def probe_media(src):
    """Just what the encode decision needs, flattened out of shared.video's ffprobe. Zeros on failure.

    Zeros rather than an exception because every caller treats an unprobeable file as "decide from what
    you can see": plan_watch_encode reports it cannot read the duration, and the size guard takes over.
    """
    out = {"duration": 0.0, "size": 0, "width": 0, "height": 0, "v_kbps": 0, "a_kbps": 0, "channels": 0}
    try:
        d = ffprobe_json(src)
        fmt = d.get("format") or {}
        out["duration"] = float(fmt.get("duration") or 0)
        out["size"] = int(fmt.get("size") or 0)
        for s in d.get("streams") or []:
            kbps = int(float(s.get("bit_rate") or 0) / 1000)
            if s.get("codec_type") == "video" and not out["width"]:
                out.update(width=int(s.get("width") or 0), height=int(s.get("height") or 0), v_kbps=kbps)
            elif s.get("codec_type") == "audio" and not out["channels"]:
                out.update(channels=int(s.get("channels") or 0), a_kbps=kbps)
    except Exception as e:
        diag(f"[WATCH] probe_media failed: {str(e)[:120]}", flush=True)
    return out


def plan_watch_encode(info, budget_b64_mb=None):
    """Decide how (or whether) to re-encode a video so its base64 fits one request.

    Returns one of:
      · {"reuse": True}                       source already fits and needs no normalising — send it
      · {"height":…, "v_kbps":…, "a_kbps":…}  re-encode at these
      · {"error": "…"}                        no rung fits; the caller should build a memory instead

    Two rules: the target bitrate is derived from the byte budget and this video's duration rather
    than fixed, since a constant cannot serve both 2 and 20 minutes; and it is capped at what the
    source has, so re-encoding can only shrink.
    """
    budget_mb = WATCH_MAX_B64_MB if budget_b64_mb is None else budget_b64_mb
    # 8% headroom: -b:v is a target an encoder may overshoot, and planning right up to the limit means
    # paying for a full encode only to have the measured-size backstop reject it.
    budget_bytes = budget_mb * 1048576 * 3 / 4 * 0.92  # base64 carries 4 bytes per 3 of payload
    dur = info.get("duration") or 0
    if dur <= 0:
        return {"error": "cannot read the video's duration"}
    afford_kbps = budget_bytes * 8 / dur / 1000  # total bitrate this duration can afford
    src_h, src_v, src_a = info.get("height") or 0, info.get("v_kbps") or 0, info.get("a_kbps") or 0
    a_kbps = min(WATCH_ABITRATE_K, src_a) if src_a else WATCH_ABITRATE_K

    # Small enough and no taller than the top rung — re-encoding could only make it worse.
    top_h = WATCH_LADDER[0][0]
    if info.get("size") and info["size"] <= budget_bytes and src_h and src_h <= top_h:
        return {"reuse": True}

    for height, floor_kbps in WATCH_LADDER:
        v_kbps = int(afford_kbps - a_kbps)
        if src_v:
            v_kbps = min(v_kbps, src_v)  # never inflate: raising the bitrate only pads the file
        if v_kbps >= floor_kbps:
            # Clamp rather than skip: for a source shorter than every rung the rungs are bitrate
            # floors at its own height, since scaling up would cost bits and add nothing.
            return {"height": min(height, src_h) if src_h else height, "v_kbps": v_kbps, "a_kbps": a_kbps}
    lowest = WATCH_LADDER[-1]
    return {
        "error": f"{dur / 60:.1f} min needs at least {lowest[1] + a_kbps} kbps at {lowest[0]}p, but "
        f"{budget_mb:.0f} MB of payload only affords {afford_kbps:.0f} kbps"
    }


def transcode_whole(src, out_path, height, vbitrate, abitrate=f"{WATCH_ABITRATE_K}k"):
    """Re-encode a WHOLE video to a profile chosen by plan_watch_encode. Returns out_path.

    The profile is passed in because the correct value depends on the video's duration and on what the
    source already is — see plan_watch_encode. Audio is forced to mono; speaker attribution reads lip
    movement, not stereo.

    Written via .tmp + os.replace so concurrent calls cannot leave a partial file for the next one to
    read as a cache hit. -f mp4 is explicit because ffmpeg would otherwise infer the container from the
    temp file's ".tmp" extension.
    """
    tmp = f"{out_path}.tmp"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    last_err = ""
    vf = ["-vf", f"scale=-2:{height}"] if height else []
    for vcodec in ("libx264", "libopenh264"):
        cmd = [
            find_tool("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-i",
            src,
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
            abitrate,
            "-ac",
            "1",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            tmp,
        ]
        t = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=WATCH_ENCODE_TIMEOUT)
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, out_path)
            diag(
                f"[WATCH] transcoded {os.path.basename(src)} -> {height}p/{vbitrate}/a{abitrate} mono in "
                f"{time.time() - t:.1f}s ({os.path.getsize(src) // 1024}KB -> {os.path.getsize(out_path) // 1024}KB)",
                flush=True,
            )
            return out_path
        last_err = (r.stderr or "")[-300:]
    if os.path.exists(tmp):
        os.remove(tmp)
    raise RuntimeError(f"ffmpeg transcode failed (libx264/libopenh264): {last_err}")


# watch_and_answer sends minutes of video in one request, so it needs longer than a 30s clip does.
WATCH_CALL_TIMEOUT = int(os.environ.get("MEM_WATCH_CALL_TIMEOUT", "900") or "900")


# ============================================================ ANSWER
class WatchError(RuntimeError):
    """A watch_and_answer attempt that produced no answer, tagged with WHY.

    `kind` decides the caller's next move, so it matters more than the message:
      · "rate"    throttling that outlived its retries — transient, try again later
      · "config"  wrong endpoint or credentials (missing model, bad key, no access)
      · "reject"  the endpoint refused the request itself (too large, context exceeded)
      · "timeout" the stream stalled or the call timed out, twice
      · "empty"   the model answered nothing, repeatedly

    Only "reject", "timeout" and "empty" are worth answering with a build. A build runs against the
    same endpoint, so for "rate" and "config" it would spend tens of minutes reproducing the failure.
    """

    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind

    @property
    def transient(self):
        """True when retrying the same call could succeed, so a build is the wrong answer."""
        return self.kind == "rate"

    @property
    def build_helps(self):
        """Whether building a memory is a sensible response. False for conditions a build would hit
        just as hard — throttling and misconfiguration both live on the path a build also takes."""
        return self.kind in {"reject", "timeout", "empty"}


def replay_answer_stream(client, video_uris, evidence_text, query, max_retries=5, model_override=None):
    """Answer by RE-WATCHING one or more clips (chronological) + text context. Yields accumulating
    text. `video_uris` may be a single data-URI string or a list of them."""
    if isinstance(video_uris, str):
        video_uris = [video_uris]
    content = [{"type": "video_url", "video_url": {"url": u}, "use_audio_in_video": True} for u in video_uris]
    content.append(
        {
            "type": "text",
            "text": REPLAY_ANSWER_PROMPT.replace("{{EVIDENCE}}", evidence_text).replace("{{QUERY}}", query),
        }
    )
    messages = [{"role": "user", "content": content}]
    diag(f"[MEM] ▸ REPLAY-ANSWER q={query!r} clips={len(video_uris)}", flush=True)
    retry_reset()
    for attempt in range(1, max_retries + 1):
        try:
            stream = client.chat.completions.create(
                model=(model_override or MODEL),
                messages=messages,
                modalities=["text"],
                **TEMP_KWARGS,
                stream=True,
                stream_options={"include_usage": True},
                timeout=600,
            )
            out = ""
            for ch in iter_deadline(stream):
                if ch.choices and ch.choices[0].delta and getattr(ch.choices[0].delta, "content", None):
                    out += ch.choices[0].delta.content
                    yield out
            if out.strip():
                return
            yield "(no output from the model, retrying…)"
            sleep_note(attempt, 2 * attempt, "empty")
        except Exception as e:
            if is_rate_limit(e):
                if attempt >= max_retries:
                    yield "⚠️ rate-limited by the endpoint; try again shortly."
                    return
                yield f"⏳ rate-limited, retrying ({attempt}/{max_retries})…"
                sleep_note(attempt, backoff(attempt, True), "rate")
            else:
                yield f"⚠️ failed: {str(e)[:200]}"
                return


def watch_answer(client, video_uri, query, max_retries=4, model_override=None):
    """Answer a question by watching ONE whole video in a single call. Returns the answer text.

    Unlike replay_answer_stream this does not yield, and it does not turn a failure into a string that
    reads like an answer: it raises WatchError with a `kind`, because the caller's fallback (build a
    memory instead) must not fire on throttling and must fire on a rejected payload. Streaming is
    pointless here — the MCP tool drains it anyway — so the final text is simply returned.
    """
    content = [
        {"type": "video_url", "video_url": {"url": video_uri}, "use_audio_in_video": True},
        {"type": "text", "text": WATCH_ANSWER_PROMPT.replace("{{QUERY}}", query)},
    ]
    diag(f"[WATCH] ▸ WATCH-ANSWER q={query!r}", flush=True)
    retry_reset()
    last, empties = "", 0
    for attempt in range(1, max_retries + 1):
        try:
            stream = client.chat.completions.create(
                model=(model_override or MODEL),
                messages=[{"role": "user", "content": content}],
                modalities=["text"],
                **TEMP_KWARGS,
                stream=True,
                stream_options={"include_usage": True},
                timeout=WATCH_CALL_TIMEOUT,
            )
            out = ""
            for ch in iter_deadline(stream):
                if ch.choices and ch.choices[0].delta and getattr(ch.choices[0].delta, "content", None):
                    out += ch.choices[0].delta.content
            if out.strip():
                return out.strip()
            empties += 1
            last = "model returned no text"
            sleep_note(attempt, 2 * attempt, "empty")
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:300]}"
            # Log the message, not just the category. Everything downstream branches on the category —
            # whether to retry, whether to tell the caller to build a memory instead — so a log that
            # records only "(error)" leaves no way to tell a misclassification from a real refusal.
            if is_rate_limit(last):
                kind = "rate"
            elif _is_misconfigured(last):
                kind = "config"
            elif _is_stall(last):
                kind = "timeout"
            else:
                kind = "reject"
            diag(f"[WATCH] attempt {attempt}/{max_retries} failed as {kind}: {last}", flush=True)
            if kind == "rate":
                if attempt >= max_retries:
                    raise WatchError(last, kind) from e
                sleep_note(attempt, backoff(attempt, True), "rate")
                continue
            # A stall gets one more go; anything the endpoint refuses outright it will refuse again, so
            # a second upload of the same payload buys nothing.
            if kind == "timeout" and attempt < 2:
                sleep_note(attempt, backoff(attempt, False), "error")
                continue
            raise WatchError(last, kind) from e
    raise WatchError(last or "no answer", "empty" if empties else "reject")
