#!/usr/bin/env python3
"""Print resumable Omni ChatCut video-translation project state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CAPABILITY = Path(__file__).resolve().parents[2]
SRC = CAPABILITY.parents[1]
for value in (str(SRC), str(CAPABILITY)):
    if value not in sys.path:
        sys.path.insert(0, value)

from qwen_mm_plugins_omni_chatcut.video_translation.project import inspect_project  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    args = parser.parse_args()
    print(json.dumps(inspect_project(args.project_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
