"""Parse numbered SRT utterances returned by Omni without another model call."""

from __future__ import annotations

import math
import re

from shared.video import parse_time

_CLOCK = r"\d+(?::\d+){1,2}(?:[,.]\d+)?"
_TIMING = re.compile(rf"^[ \t]*(?P<start>{_CLOCK})[ \t]*-->[ \t]*(?P<end>{_CLOCK})[ \t]*$", re.MULTILINE)
_COUNTER = re.compile(r"^[ \t]*(\d+)[ \t]*\n\Z", re.MULTILINE)


def _cue_headers(text: str, timings: list[re.Match]) -> list[tuple[int, int | None]]:
    """Locate counters at block boundaries or in an established consecutive sequence.

    A numeric speech line followed by a blank separator belongs to the preceding cue.
    Without separators or a numbering sequence, keep ambiguous numeric lines as speech.
    """
    headers = []
    previous_counter = None
    for index, timing in enumerate(timings):
        offset = timings[index - 1].end() if index else 0
        prefix = text[offset : timing.start()]
        counter = None
        start = timing.start()
        match = _COUNTER.search(prefix)
        if match:
            candidate = int(match[1])
            before = prefix[: match.start()]
            separated = re.search(r"\n[ \t]*\n\Z", before) is not None
            consecutive = previous_counter is not None and candidate == previous_counter + 1 and bool(before.strip())
            if index == 0 or separated or consecutive:
                counter = candidate
                start = offset + match.start()
        headers.append((start, counter))
        previous_counter = counter
    return headers


def parse_srt_transcript(text: str, *, warnings: list[str] | None = None) -> list[dict]:
    """Recover SRT cues despite missing counters, separators, hours, or timeline mistakes."""
    warnings = warnings if warnings is not None else []
    if not isinstance(text, str):
        raise ValueError("expected an SRT transcript")
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    fence = re.fullmatch(r"```(?:srt)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence[1].strip()
    if not text:
        return []
    timings = list(_TIMING.finditer(text))
    if not timings:
        raise ValueError("no recognizable SRT timecodes")
    headers = _cue_headers(text, timings)
    segments = []
    previous_start = 0.0
    for index, timing in enumerate(timings, 1):
        if headers[index - 1][1] != index:
            warnings.append("SRT counters are missing or nonsequential; output counters were regenerated.")
        start, end = (parse_time(timing[key].replace(",", ".")) for key in ("start", "end"))
        if not math.isfinite(start) or not math.isfinite(end):
            warnings.append("A cue has unreadable timestamps; returning the remaining cues and raw reply.")
            continue
        if timing["start"].count(":") != 2 or timing["end"].count(":") != 2:
            warnings.append("SRT timecodes omitted hours; interpreted them as minutes and seconds.")
        if end < start or start < previous_start:
            warnings.append("Some SRT timestamps are reversed or out of order; model values are retained in JSON.")
        stop = headers[index][0] if index < len(timings) else len(text)
        content = text[timing.end() : stop].strip()
        if not content:
            warnings.append("A cue has no speech text; returning the remaining cues and raw reply.")
            continue
        if "-->" in content:
            warnings.append("Some SRT content has unrecognized timecodes; the raw reply is retained.")
        segments.append({"start": start, "end": end, "text": content})
        previous_start = start
    if not segments:
        raise ValueError("no SRT cues with speech text")
    return segments
