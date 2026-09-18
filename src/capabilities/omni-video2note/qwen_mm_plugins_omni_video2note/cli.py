"""Command-line adapter for the Omni Video2Note pipeline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert a local tutorial video into an audited illustrated PDF.")
    parser.add_argument("video_path", help="Path to the local tutorial video.")
    parser.add_argument("--output-path", required=True, help="Destination PDF path.")
    parser.add_argument("--language", default="auto", help="Language for the generated note (default: auto).")
    parser.add_argument("--title", help="Use this exact document title.")
    parser.add_argument("--overwrite", action="store_true", help="Replace the existing PDF after successful generation.")
    parser.add_argument("--quality-profile", default="balanced", help="Pipeline quality profile (default: balanced).")
    parser.add_argument("--omni-model", help="Audio-visual understanding model override.")
    parser.add_argument("--vl-model", help="Planning and writing model override.")
    parser.add_argument("--review-model", help="Candidate and PDF review model override (default: resolved VL model).")
    parser.add_argument("--font", help="Regular PDF font path.")
    parser.add_argument("--bold-font", help="Bold PDF font path.")
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument(
        "--no-asr",
        action="store_true",
        help="Ignore the video's audio and speech during Omni understanding.",
    )
    audio.add_argument(
        "--require-asr",
        action="store_true",
        help="Require an audio track and have Omni understand it; no separate ASR model is used.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate only; never create output or call a model.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the shared pipeline runner and return its documented exit_code."""
    args = vars(_parser().parse_args(argv))
    try:
        from .pipeline.runner import run_video2note

        result: Any = run_video2note(**args)
        if not isinstance(result, dict) or not isinstance(result.get("exit_code"), int):
            raise TypeError("run_video2note must return a JSON object containing an integer exit_code")
    except Exception as exc:  # noqa: BLE001 — CLI failures use the same structured result contract
        result = {"exit_code": 1, "status": "failed", "error": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
