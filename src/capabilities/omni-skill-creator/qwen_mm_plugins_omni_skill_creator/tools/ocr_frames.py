"""MCP tool: OCR on-screen text from video frames via tesseract."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import (
    _LANGS_RE,
    MAX_OCR_FRAMES,
    MediaOpsError,
    _run,
    clamp_timestamps,
    ffmpeg_path,
    format_mmss,
    get_video_metadata,
    validate_output_dir,
    validate_video_path,
)


class OcrFramesArgs(BaseModel):
    video_path: str = Field()
    timestamps: list[float] = Field()
    langs: str = Field(default="eng")
    locate: list[str] = Field(default_factory=list)


TOOL: dict[str, Any] = {"name": "ocr_frames", "args": OcrFramesArgs}


def _parse_tsv_lines(tsv: str) -> list[dict[str, Any]]:
    """Group tesseract TSV words into text lines with pixel bounds.

    Columns: level page block par line word left top width height conf text.
    Words (level 5) are grouped by (block, par, line); a space is inserted between
    neighbours only when neither side is CJK, so Chinese labels stay unspaced.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        if not text:
            continue
        try:
            left, top, width, height = (int(cols[i]) for i in range(6, 10))
            conf = float(cols[10])
        except ValueError:
            continue
        groups.setdefault((cols[2], cols[3], cols[4]), []).append(
            {"text": text, "box": [left, top, left + width, top + height], "conf": conf}
        )

    lines: list[dict[str, Any]] = []
    for words in groups.values():
        words.sort(key=lambda w: w["box"][0])
        text = words[0]["text"]
        for prev, cur in zip(words, words[1:]):
            joiner = "" if _is_cjk(prev["text"][-1]) and _is_cjk(cur["text"][0]) else " "
            text += joiner + cur["text"]
        lines.append(
            {
                "text": text,
                "bbox_px": [
                    min(w["box"][0] for w in words),
                    min(w["box"][1] for w in words),
                    max(w["box"][2] for w in words),
                    max(w["box"][3] for w in words),
                ],
                "conf": round(sum(w["conf"] for w in words) / len(words), 1),
                "words": [{"text": w["text"], "bbox_px": w["box"]} for w in words],
            }
        )
    lines.sort(key=lambda ln: (ln["bbox_px"][1], ln["bbox_px"][0]))
    return lines


def _is_cjk(ch: str) -> bool:
    return "\u3000" <= ch <= "\u9fff"


def _match_lines(lines: list[dict[str, Any]], queries: list[str]) -> list[dict[str, Any]]:
    """Lines whose text contains a query, carrying the tightest box for the query itself."""
    matches: list[dict[str, Any]] = []
    for query in queries:
        needle = query.strip().lower()
        if not needle:
            continue
        for line in lines:
            if needle not in line["text"].lower():
                continue
            hit_words = [w for w in line["words"] if w["text"].lower() in needle or needle in w["text"].lower()]
            box = line["bbox_px"]
            if hit_words:
                box = [
                    min(w["bbox_px"][0] for w in hit_words),
                    min(w["bbox_px"][1] for w in hit_words),
                    max(w["bbox_px"][2] for w in hit_words),
                    max(w["bbox_px"][3] for w in hit_words),
                ]
            matches.append({"query": query, "line": line["text"], "bbox_px": box, "conf": line["conf"]})
    return matches


def _tsv_output(binary: str, frame_path: str, langs: str) -> str:
    """Tesseract TSV for one image, however this install exposes it.

    Three ways, in order, because the pod's tesseract is a `dpkg -x` tree rather than an apt
    install and we can't assume the whole tessdata layout:

    1. `… stdout … tsv` — the trailing `tsv` is not a flag, it's a config FILE tesseract reads
       from `$TESSDATA_PREFIX/configs/tsv`. A trimmed tree that carries only the
       `.traineddata` models dies here on "cannot read init file".
    2. `… stdout … -c tessedit_create_tsv=1` — the same parameter set at runtime, no file
       needed. Whether stdout then carries TSV is a property of the renderer, so the caller
       validates the header instead of trusting it.
    3. `… <base> … -c tessedit_create_tsv=1` then read `<base>.tsv` — file output is the
       documented path, so this holds even if stdout doesn't.
    """
    base_cmd = [binary, frame_path]
    tail = ["-l", langs]
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "ocr")
        attempts = (
            (base_cmd + ["stdout", *tail, "tsv"], None),
            (base_cmd + ["stdout", *tail, "-c", "tessedit_create_tsv=1"], None),
            (base_cmd + [base, *tail, "-c", "tessedit_create_tsv=1"], f"{base}.tsv"),
        )
        last = ""
        for cmd, out_file in attempts:
            try:
                proc = _run(cmd, timeout=60)
            except MediaOpsError as exc:
                last = str(exc)
                continue
            if out_file:
                if not os.path.isfile(out_file):
                    last = f"`{' '.join(cmd[2:])}` wrote no {os.path.basename(out_file)}"
                    continue
                with open(out_file, encoding="utf-8", errors="replace") as fh:
                    out = fh.read()
            else:
                out = proc.stdout or ""
            first = out.splitlines()[0] if out.strip() else ""
            if first.startswith("level\t") or "\tconf\t" in first:
                return out
            last = f"`{' '.join(cmd[2:])}` returned no TSV header"
    raise MediaOpsError(f"tesseract produced no TSV: {last}")


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""OCR on-screen text (commands, code, menus) from video frames via tesseract. Cheaper and faster than
    VLM for legible screen text. Pass `locate` to also get the PIXEL BOX of a named button/menu/label —
    the way to annotate a text-bearing target without estimating its position by eye. Degrades
    gracefully when tesseract is not installed.

    Args:
        video_path: Path to the source video file.
        timestamps: Timestamps (seconds) of frames to OCR.
        langs: Tesseract language code(s), e.g. 'eng' or 'eng+chi_sim'.
        locate: On-screen strings you want the PIXEL BOX of — a button, menu item, label, or field you are about
            to annotate ('Slide Zoom', 'Remove Background'). Matched case-insensitively against each OCR'd text
            line; every match comes back with `bbox_px` in full-frame pixels, ready to hand to `image_annotate`
            with `coord_space="pixel"`. Leave empty for plain text only.
    """
    raw_path = arguments["video_path"]
    timestamps = arguments.get("timestamps", [])
    langs = arguments.get("langs", "eng")
    locate = [q for q in (arguments.get("locate") or []) if str(q).strip()]

    binary = shutil.which("tesseract")
    if binary is None:
        result = {
            "available": False,
            "results": [],
            "reason": (
                "OCR is unavailable: `tesseract` is not installed or not on "
                "PATH. Proceed using audio timeline and keyframe viewing only."
            ),
        }
        return [{"type": "text", "text": json.dumps(result, indent=2)}]

    if not _LANGS_RE.match(langs):
        raise MediaOpsError("`langs` must look like `eng` or `eng+chi_sim`.")

    path = validate_video_path(raw_path)
    meta = get_video_metadata(str(path))
    points = clamp_timestamps(timestamps or [], meta.duration_sec, MAX_OCR_FRAMES, hard_cap=MAX_OCR_FRAMES)
    if not points:
        raise MediaOpsError("`timestamps` must contain at least one value.")

    scratch = validate_output_dir(None) / "ocr"
    scratch.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for ts in points:
        frame_path = scratch / f"{format_mmss(ts)}.png"
        _run(
            [
                ffmpeg_path(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{ts:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-y",
                str(frame_path),
            ]
        )
        if not frame_path.is_file():
            continue
        try:
            proc = _run(
                [binary, str(frame_path), "stdout", "-l", langs],
                timeout=60,
            )
            text = (proc.stdout or "").strip()
        except MediaOpsError as exc:
            result = {
                "available": False,
                "results": results,
                "reason": f"tesseract failed: {exc}",
            }
            return [{"type": "text", "text": json.dumps(result, indent=2)}]
        results.append({"seconds": ts, "text": text})
        if locate:
            entry = results[-1]
            try:
                tsv = _tsv_output(binary, str(frame_path), langs)
            except MediaOpsError as exc:
                entry["locate_error"] = str(exc)
            else:
                entry["matches"] = _match_lines(_parse_tsv_lines(tsv), locate)

    result = {
        "available": True,
        "langs": langs,
        "frame_size": {"w": meta.width, "h": meta.height},
        "results": results,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
