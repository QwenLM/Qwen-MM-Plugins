"""Parse subtitle files (SRT, VTT) into text blocks."""

from __future__ import annotations

import os
import re
from typing import Any


def render(path: str, **opts: Any) -> list[dict[str, Any]]:
    """Return subtitle content as MCP text blocks."""
    ext = os.path.splitext(path)[1].lower()

    # SRT/VTT files are UTF-8 text. Read explicitly as UTF-8 so parsing does not
    # depend on the host default encoding (locale-dependent on Windows), which
    # otherwise corrupts non-ASCII captions. utf-8-sig transparently strips a
    # leading BOM when present.
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()

    if ext == ".vtt":
        entries = _parse_vtt(text)
    else:
        entries = _parse_srt(text)

    if not entries:
        return [{"type": "text", "text": f"Subtitle: {path} (empty)"}]

    lines = [f"Subtitle: {path} | {len(entries)} entries"]
    for ts, content in entries:
        lines.append(f"[{ts}] {content}")

    return [{"type": "text", "text": "\n".join(lines)}]


def _parse_srt(text: str) -> list[tuple[str, str]]:
    blocks = re.split(r"\n\s*\n", text.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        ts_line = None
        content_lines = []
        for line in lines:
            if "-->" in line:
                ts_line = line.strip()
            elif ts_line is not None:
                content_lines.append(line.strip())
        if ts_line and content_lines:
            entries.append((ts_line, " ".join(content_lines)))
    return entries


def _parse_vtt(text: str) -> list[tuple[str, str]]:
    lines = text.split("\n")
    entries = []
    i = 0
    while i < len(lines):
        if "-->" in lines[i]:
            ts = lines[i].strip()
            content_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                content_lines.append(lines[i].strip())
                i += 1
            if content_lines:
                entries.append((ts, " ".join(content_lines)))
        else:
            i += 1
    return entries
