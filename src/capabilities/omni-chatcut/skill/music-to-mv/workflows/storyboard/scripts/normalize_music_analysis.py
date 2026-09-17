#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def seconds(value):
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours = "0"
        minutes, rest = parts
    elif len(parts) == 3:
        hours, minutes, rest = parts
    else:
        raise ValueError(f"unsupported timestamp: {value}")
    return round(int(hours) * 3600 + int(minutes) * 60 + float(rest), 3)


def section(text, heading, next_heading=None):
    start = re.search(rf"^##\s+[^\n]*{re.escape(heading)}[^\n]*$", text, re.MULTILINE | re.IGNORECASE)
    if not start:
        return ""
    body_start = start.end()
    if next_heading:
        end = re.search(
            rf"^##\s+[^\n]*{re.escape(next_heading)}[^\n]*$", text[body_start:], re.MULTILINE | re.IGNORECASE
        )
        if end:
            return text[body_start : body_start + end.start()].strip()
    next_any = re.search(r"^##\s+", text[body_start:], re.MULTILINE)
    if next_any:
        return text[body_start : body_start + next_any.start()].strip()
    return text[body_start:].strip()


def parse_structure(body):
    timestamp = r"(?:\d{2}:\d{2}:\d{2}|\d{2}:\d{2})[,.]\d{3}"
    pattern = re.compile(
        rf"^###[ \t]+\[({timestamp})[ \t]+-->[ \t]+({timestamp})\][ \t]+\[([^\]\n]+)\][ \t]*$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(body))
    structure = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        caption = body[match.end() : end].strip()
        caption = re.sub(r"\ACaption:[ \t]*", "", caption).strip()
        structure.append(
            {
                "start_sec": seconds(match.group(1)),
                "end_sec": seconds(match.group(2)),
                "label": match.group(3).strip(),
                "caption": caption,
            }
        )
    return structure


def parse_lyrics(body):
    pattern = re.compile(
        r"^(\d+)\s*$\n(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*$\n([^\n]+)",
        re.MULTILINE,
    )
    return [
        {
            "index": int(match.group(1)),
            "start_sec": seconds(match.group(2)),
            "end_sec": seconds(match.group(3)),
            "text": "" if match.group(4).strip().lower() == "[instrumental]" else match.group(4).strip(),
            "instrumental": match.group(4).strip().lower() == "[instrumental]",
        }
        for match in pattern.finditer(body)
    ]


def parse_summaries(body):
    matches = list(re.finditer(r"^###\s+([^\n]+)\s*$", body, re.MULTILINE))
    summaries = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        summaries[match.group(1).strip()] = body[match.end() : end].strip()
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis")
    parser.add_argument("--output")
    args = parser.parse_args()

    source = Path(args.analysis)
    text = source.read_text(encoding="utf-8")
    overview = section(text, "Whole-Track Overview", "Structure Timeline")
    structure_body = section(text, "Structure Timeline", "Lyrics Timeline")
    lyrics_body = section(text, "Lyrics Timeline", "Overall Summary")
    summary_body = section(text, "Overall Summary")

    structure = parse_structure(structure_body)
    lyrics = parse_lyrics(lyrics_body)
    candidates = [0.0]
    candidates.extend(item["end_sec"] for item in structure)
    candidates.extend(item["end_sec"] for item in lyrics)

    result = {
        "source": str(source),
        "duration_sec": round(max(candidates), 3),
        "whole_track_overview": overview,
        "structure": structure,
        "lyrics": lyrics,
        "overall_summary": parse_summaries(summary_body),
        "diagnostics": {
            "structure_count": len(structure),
            "lyric_count": len(lyrics),
        },
    }
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
