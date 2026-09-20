#!/usr/bin/env python3
"""Migrate a grouped storyboard to one editorial shot per provider segment."""

import argparse
import copy
import json
from pathlib import Path


def split_segments(board):
    migrated = copy.deepcopy(board)
    output_segments = []
    for source_segment in board.get("segments", []):
        windows = {
            window.get("sub_global_index"): window
            for window in source_segment.get("shot_windows", [])
            if isinstance(window, dict)
        }
        for shot in source_segment.get("sub_shots", []):
            shot = copy.deepcopy(shot)
            segment_index = len(output_segments)
            shot["seg_index"] = segment_index
            shot["sub_index"] = 0
            duration = float(shot["duration_sec"])
            previous_window = windows.get(shot.get("global_index"), {})
            output_segments.append(
                {
                    "index": segment_index,
                    "start_sec": shot["start_sec"],
                    "end_sec": shot["end_sec"],
                    "duration_sec": shot["duration_sec"],
                    "assembly_mode": "single_take_i2v",
                    "scene_ids": [shot["scene_id"]] if shot.get("scene_id") else [],
                    "cast_present": list(shot.get("cast_present", [])),
                    "sub_shots": [shot],
                    "shot_windows": [
                        {
                            "t": [0, duration],
                            "sub_global_index": shot["global_index"],
                            "what": previous_window.get("what") or shot.get("shot_summary", ""),
                        }
                    ],
                }
            )
    migrated["segments"] = output_segments
    return migrated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storyboard")
    parser.add_argument("output")
    args = parser.parse_args()
    source = Path(args.storyboard).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if source == output:
        raise SystemExit("Refusing to overwrite the source storyboard; choose a new output path")
    board = json.loads(source.read_text(encoding="utf-8"))
    migrated = split_segments(board)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
