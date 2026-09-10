"""The omni calls a build makes: one composite video+audio call per clip, and a text-only call for the
passes that work off accumulated text (semantic induction, name alignment).

Streaming with a stall deadline, 429 classification and backoff all live in omni_core, because the
query side's watch and replay calls need exactly the same handling. What stays here is build-shaped:
the timeouts assume a per-clip video call, and the retry counts assume a job that runs for tens of
minutes and must not give up on a transient error.

MODEL is read through the module (omni_core.MODEL) rather than imported by name, because --model
rebinds it via omni_core.set_chat_model before the first call and a from-import would freeze the
default at import time.
"""

import json
import os
import re

import omni_core
from omni_core import TEMP_KWARGS, b64_uri, backoff, is_rate_limit, iter_deadline, retry_reset, sleep_note

# ----------------------------------------------------------------------------- helpers
_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)


def parse_json_block(text):
    m = _FENCE.search(text)
    cand = m.group(1).strip() if m else None
    if cand is None:
        i, j = text.find("{"), text.rfind("}")
        cand = text[i : j + 1] if (i != -1 and j != -1 and j > i) else None
    try:
        return json.loads(cand) if cand else None
    except Exception:
        return None


# Timeouts for video calls (extraction) and text calls (induction, planning, answering). Raise them
# when the endpoint is queueing, e.g. MEM_CALL_TIMEOUT=1200 MEM_CALL_RETRIES=12 MEM_STREAM_STALL=600.
CALL_TIMEOUT = int(os.environ.get("MEM_CALL_TIMEOUT", "240") or "240")

CALL_RETRIES = int(os.environ.get("MEM_CALL_RETRIES", "8") or "8")

TEXT_TIMEOUT = int(os.environ.get("MEM_TEXT_TIMEOUT", "600") or "600")

TEXT_RETRIES = int(os.environ.get("MEM_TEXT_RETRIES", "8") or "8")


def call_model(client, video_path, prompt, max_retries=None, timeout=None):
    """Video call (stage-1). Stream + retry create on 429. Returns {raw,parsed,usage,attempts}.
    max_retries/timeout default to CALL_RETRIES/CALL_TIMEOUT (env-overridable)."""
    max_retries = CALL_RETRIES if max_retries is None else max_retries
    timeout = CALL_TIMEOUT if timeout is None else timeout
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": b64_uri(video_path)}, "use_audio_in_video": True},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    last_err, attempts = None, 0
    retry_reset()
    for attempt in range(1, max_retries + 1):
        attempts += 1
        try:
            stream = client.chat.completions.create(
                model=omni_core.MODEL,
                messages=messages,
                modalities=["text"],
                **TEMP_KWARGS,
                stream=True,
                stream_options={"include_usage": True},
                timeout=timeout,
            )
            parts, usage, finish = [], None, None
            for ch in iter_deadline(stream):
                if ch.choices:
                    c = ch.choices[0]
                    if c.delta is not None and getattr(c.delta, "content", None):
                        parts.append(c.delta.content)
                    if c.finish_reason:
                        finish = c.finish_reason
                if getattr(ch, "usage", None):
                    usage = ch.usage
            text = "".join(parts).strip()
            if not text:
                last_err = f"empty text (finish={finish})"
                sleep_note(attempt, 3 * attempt, "empty")
                continue
            return {
                "raw": text,
                "parsed": parse_json_block(text),
                "finish": finish,
                "usage": usage.model_dump() if usage is not None else {},
                "attempts": attempts,
            }
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
            _ir = is_rate_limit(last_err)
            sleep_note(attempt, backoff(attempt, _ir), "rate" if _ir else "error")
    return {"error": last_err, "attempts": attempts}


def _stream_text(client, prompt, model=None, max_retries=None, timeout=None):
    """Text-only call (stage-2 / consolidation / PLAN). Returns {raw,parsed,usage} or {error}.
    model defaults to MODEL (omni-plus); PLAN may pass a faster text model.
    max_retries/timeout default to TEXT_RETRIES/TEXT_TIMEOUT (env-overridable)."""
    max_retries = TEXT_RETRIES if max_retries is None else max_retries
    timeout = TEXT_TIMEOUT if timeout is None else timeout
    last = None
    mdl = model or omni_core.MODEL
    retry_reset()
    for a in range(1, max_retries + 1):
        try:
            st = client.chat.completions.create(
                model=mdl,
                messages=[{"role": "user", "content": prompt}],
                **TEMP_KWARGS,
                stream=True,
                stream_options={"include_usage": True},
                timeout=timeout,
            )
            parts, usage = [], None
            for ch in iter_deadline(st):
                if ch.choices and ch.choices[0].delta and getattr(ch.choices[0].delta, "content", None):
                    parts.append(ch.choices[0].delta.content)
                if getattr(ch, "usage", None):
                    usage = ch.usage
            txt = "".join(parts).strip()
            if not txt:
                last = "empty"
                sleep_note(a, 3 * a, "empty")
                continue
            return {"raw": txt, "parsed": parse_json_block(txt), "usage": usage.model_dump() if usage else {}}
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:160]}"
            _ir = is_rate_limit(last)
            sleep_note(a, backoff(a, _ir), "rate" if _ir else "error")
    return {"error": last}
