"""Parse Omni's recommended speaker/timestamp tokens without an extra model call."""

from __future__ import annotations

import math
import re

from shared.video import parse_time

_SEGMENT = re.compile(
    r"<sos>\s*<(?P<start>[^<>]+)>(?P<text>(?:(?!<sos>|<eos>).)*?)"
    r"<(?P<end>[^<>]+)>\s*<speaker(?P<speaker>\d+)>(?:\s*<eos>)?",
    re.DOTALL,
)


def parse_speaker_transcript(text: str, *, warnings: list[str] | None = None) -> list[dict]:
    """Recover complete speaker segments even when surrounding tokens or other segments are wrong."""
    warnings = warnings if warnings is not None else []
    if not isinstance(text, str):
        raise ValueError("expected a speaker-token transcript")
    text = text.strip()
    fence = re.fullmatch(r"```[^\n]*\n(.*?)\n```", text, re.DOTALL)
    if fence:
        text = fence[1].strip()
    if not text.startswith("<soc>") or not text.endswith("<eoc>"):
        warnings.append("Speaker transcript wrapper tokens are incomplete; recovered recognizable segments.")
    body = text.removeprefix("<soc>").removesuffix("<eoc>")
    if not body.strip() and text.startswith("<soc>") and text.endswith("<eoc>"):
        return []
    segments = []
    position = 0
    for match in _SEGMENT.finditer(body):
        if body[position : match.start()].strip():
            warnings.append("Some speaker content could not be parsed; the raw reply is retained.")
        position = match.end()
        if not match[0].rstrip().endswith("<eos>"):
            warnings.append("A speaker segment omitted its closing token; recovered its text and timestamps.")
        try:
            start, end = (parse_time(match[key].replace(",", ".")) for key in ("start", "end"))
            if start is None or end is None or not math.isfinite(start) or not math.isfinite(end):
                raise ValueError("timestamps must be finite")
        except (TypeError, ValueError):
            warnings.append("A speaker segment has unreadable timestamps; recovered the remaining segments.")
            continue
        if start < 0 or end < start:
            warnings.append("Some speaker timestamps are negative or reversed; model values are retained in JSON.")
        content = match["text"].strip()
        if not content or "<sos>" in content or "<eos>" in content:
            warnings.append("A speaker segment is incomplete; recovered the remaining segments.")
            continue
        segments.append({"speaker": f"Speaker {int(match['speaker'])}", "start": start, "end": end, "text": content})
    if body[position:].strip():
        warnings.append("Some speaker content is incomplete; the raw reply is retained.")
    if not segments:
        raise ValueError("no recognizable speaker segments")
    return segments
