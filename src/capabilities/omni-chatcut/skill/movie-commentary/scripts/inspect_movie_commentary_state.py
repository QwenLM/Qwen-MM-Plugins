#!/usr/bin/env python3
"""Inspect durable Movie Commentary artifacts without importing the installed runtime."""

import argparse
import json
from pathlib import Path


def read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.project_dir).expanduser().resolve()
    manifest = read_json(root / "project.json")
    if manifest.get("schema") != "omni-chatcut/movie-commentary-project/v1":
        raise SystemExit(f"not an Omni ChatCut movie-commentary project: {root}")
    facts = read_json(root / "plan/execution_facts.json")
    facts_complete = all(
        facts.get(key) is not None
        for key in (
            "credits_start_sec",
            "source_cut_max_sec",
            "burned_subtitle_band",
            "output_canvas",
            "subtitle_style",
        )
    )
    notes = list((root / "plan/watch_notes").glob("*.md"))
    shards = sorted(root.glob("shards/shard_*.json"))
    reports = sorted(root.glob("out/shard_*/exec_report.json"))
    final_video = root / "full/commentary.mp4"
    final_qa = root / "full/orchestrator_qa_full.json"
    qa = read_json(final_qa)
    if final_video.is_file() and qa.get("overall_pass") is True:
        stage = "complete_or_review"
    elif shards:
        stage = "render_or_resume"
    elif (root / "plan/editing_plan.json").is_file():
        stage = "validate_and_shard"
    elif notes and facts_complete:
        stage = "author_plan"
    else:
        stage = "analyze_source"
    result = {
        "project_root": str(root),
        "source_movie": manifest.get("source_movie"),
        "target": manifest.get("target"),
        "execution_facts_complete": facts_complete,
        "watch_note_count": len(notes),
        "plan_exists": (root / "plan/editing_plan.json").is_file(),
        "shard_count": len(shards),
        "report_count": len(reports),
        "final_video_exists": final_video.is_file(),
        "final_qa_pass": qa.get("overall_pass") is True,
        "recommended_stage": stage,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
