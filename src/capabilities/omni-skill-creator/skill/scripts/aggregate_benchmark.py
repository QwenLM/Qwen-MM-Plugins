#!/usr/bin/env python3
"""
Aggregate individual run results into benchmark summary statistics.

Reads grading.json files from run directories and produces:
- run_summary with mean, repeat variance, cross-eval dispersion, min, and max for each metric
- a delta, but only when a second configuration was actually run

Usage:
    python aggregate_benchmark.py <benchmark_dir>

Example:
    python aggregate_benchmark.py benchmarks/2026-01-15T10-30-00/

The default L3 layout has one configuration and one run per eval:

    <benchmark_dir>/
    └── eval-N-<descriptive-name>/
        ├── eval_metadata.json
        └── with_skill/
            └── run-1/grading.json

Config directories are discovered by name rather than hardcoded, although the release
workflow writes only `with_skill`. Extra `run-K/` siblings are picked up without changing
this script. A config directory with no `run-*` child is skipped entirely, so grading.json
sitting directly under `with_skill/` never reaches the benchmark. Eval directories may also
sit under a `runs/` subdirectory.
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


def calculate_stats(values: list[float | int | None]) -> dict:
    """Calculate sample statistics without turning missing evidence into zero."""
    observed = [float(value) for value in values if value is not None]
    if not observed:
        return {"mean": None, "stddev": None, "min": None, "max": None, "n": 0}

    n = len(observed)
    mean = sum(observed) / n

    if n > 1:
        variance = sum((x - mean) ** 2 for x in observed) / (n - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = None

    return {
        "mean": round(mean, 4),
        "stddev": round(stddev, 4) if stddev is not None else None,
        "min": round(min(observed), 4),
        "max": round(max(observed), 4),
        "n": n,
    }


def _metric_summary(runs: list[dict], metric: str) -> dict:
    """Separate repeat variance from differences between distinct evals."""
    by_eval: dict[object, list[float | int | None]] = {}
    for run in runs:
        by_eval.setdefault(run["eval_id"], []).append(run.get(metric))

    per_eval = {eval_id: calculate_stats(values) for eval_id, values in by_eval.items()}
    observed = [float(run[metric]) for run in runs if run.get(metric) is not None]
    eval_means = [stats["mean"] for stats in per_eval.values() if stats["mean"] is not None]

    repeat_degrees = sum(max(stats["n"] - 1, 0) for stats in per_eval.values())
    if repeat_degrees:
        pooled_variance = (
            sum(
                (stats["n"] - 1) * (stats["stddev"] ** 2)
                for stats in per_eval.values()
                if stats["n"] > 1 and stats["stddev"] is not None
            )
            / repeat_degrees
        )
        repeat_stddev = round(math.sqrt(pooled_variance), 4)
    else:
        repeat_stddev = None

    cross_eval = calculate_stats(eval_means)
    mean = sum(eval_means) / len(eval_means) if eval_means else None
    return {
        "mean": round(mean, 4) if mean is not None else None,
        "repeat_stddev": repeat_stddev,
        "cross_eval_stddev": cross_eval["stddev"],
        "min": round(min(observed), 4) if observed else None,
        "max": round(max(observed), 4) if observed else None,
        "n_runs": len(observed),
        "n_evals": len(eval_means),
    }


def load_run_results(benchmark_dir: Path) -> dict:
    """
    Load all run results from a benchmark directory.

    Returns dict keyed by config name (normally just "with_skill"), each
    containing a list of run results.
    """
    # Support both layouts: eval dirs directly under benchmark_dir, or under runs/
    runs_dir = benchmark_dir / "runs"
    if runs_dir.exists():
        search_dir = runs_dir
    elif list(benchmark_dir.glob("eval-*")):
        search_dir = benchmark_dir
    else:
        print(f"No eval directories found in {benchmark_dir} or {benchmark_dir / 'runs'}")
        return {}

    results: dict[str, list] = {}

    for eval_idx, eval_dir in enumerate(sorted(search_dir.glob("eval-*"))):
        metadata_path = eval_dir / "eval_metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path) as mf:
                    metadata = json.load(mf)
                    eval_id = metadata.get("eval_id", eval_idx)
                    eval_name = metadata.get("eval_name", eval_dir.name)
            except (json.JSONDecodeError, OSError):
                eval_id = eval_idx
                eval_name = eval_dir.name
        else:
            try:
                eval_id = int(eval_dir.name.split("-")[1])
            except ValueError:
                eval_id = eval_idx
            eval_name = eval_dir.name

        # Discover config directories dynamically rather than hardcoding names
        for config_dir in sorted(eval_dir.iterdir()):
            if not config_dir.is_dir():
                continue
            # Skip non-config directories (inputs, outputs, etc.)
            if not list(config_dir.glob("run-*")):
                continue
            config = config_dir.name
            if config not in results:
                results[config] = []

            for run_dir in sorted(config_dir.glob("run-*")):
                run_number = int(run_dir.name.split("-")[1])
                grading_file = run_dir / "grading.json"

                if not grading_file.exists():
                    print(f"Warning: grading.json not found in {run_dir}")
                    continue

                try:
                    with open(grading_file) as f:
                        grading = json.load(f)
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON in {grading_file}: {e}")
                    continue

                # Extract metrics
                summary = grading.get("summary", {})
                result = {
                    "eval_id": eval_id,
                    "eval_name": eval_name,
                    "run_number": run_number,
                    "pass_rate": summary.get("pass_rate"),
                    "passed": summary.get("passed"),
                    "failed": summary.get("failed"),
                    "total": summary.get("total"),
                    "grading_provenance": grading.get("grading_provenance"),
                }
                if result["grading_provenance"] is None:
                    print(f"Warning: grading_provenance not found in {grading_file}; independence is UNKNOWN")

                # Extract timing — check grading.json first, then sibling timing.json
                timing = grading.get("timing", {})
                result["time_seconds"] = timing.get("total_duration_seconds")
                result["tokens"] = timing.get("total_tokens")
                timing_file = run_dir / "timing.json"
                if (result["time_seconds"] is None or result["tokens"] is None) and timing_file.exists():
                    try:
                        with open(timing_file) as tf:
                            timing_data = json.load(tf)
                        if result["time_seconds"] is None:
                            result["time_seconds"] = timing_data.get("total_duration_seconds")
                        if result["tokens"] is None:
                            result["tokens"] = timing_data.get("total_tokens")
                    except (json.JSONDecodeError, OSError):
                        pass

                # Extract metrics if available
                metrics = grading.get("execution_metrics", {})
                result["tool_calls"] = metrics.get("total_tool_calls")
                result["errors"] = metrics.get("errors_encountered")

                # Extract expectations — benchmark.json requires fields: text, passed, evidence
                raw_expectations = grading.get("expectations", [])
                for exp in raw_expectations:
                    if "text" not in exp or "passed" not in exp:
                        print(
                            f"Warning: expectation in {grading_file} missing required fields (text, passed, evidence): {exp}"
                        )
                result["expectations"] = raw_expectations

                # Extract notes from user_notes_summary
                notes_summary = grading.get("user_notes_summary", {})
                notes = []
                notes.extend(notes_summary.get("uncertainties", []))
                notes.extend(notes_summary.get("needs_review", []))
                notes.extend(notes_summary.get("workarounds", []))
                result["notes"] = notes

                results[config].append(result)

    return results


def aggregate_results(results: dict) -> dict:
    """
    Aggregate run results into summary statistics.

    Returns run_summary with stats for each configuration, plus a delta when
    there is a second configuration to compare against.
    """
    run_summary = {}
    configs = list(results.keys())

    for config in configs:
        runs = results.get(config, [])

        run_summary[config] = {
            "pass_rate": _metric_summary(runs, "pass_rate"),
            "time_seconds": _metric_summary(runs, "time_seconds"),
            "tokens": _metric_summary(runs, "tokens"),
        }

    # A delta needs a second configuration to subtract. With only one, there is
    # nothing to compare against — and a "+0.85" measured against an absent
    # baseline reads as an improvement that was never demonstrated, so omit the
    # key entirely rather than treating a missing config as a zero.
    if len(configs) >= 2:
        primary = run_summary.get(configs[0], {})
        baseline = run_summary.get(configs[1], {})

        def delta(metric: str, digits: int) -> str | None:
            primary_mean = primary.get(metric, {}).get("mean")
            baseline_mean = baseline.get(metric, {}).get("mean")
            if primary_mean is None or baseline_mean is None:
                return None
            return f"{primary_mean - baseline_mean:+.{digits}f}"

        run_summary["delta"] = {
            "pass_rate": delta("pass_rate", 2),
            "time_seconds": delta("time_seconds", 1),
            "tokens": delta("tokens", 0),
        }

    return run_summary


def generate_benchmark(benchmark_dir: Path, skill_name: str = "", skill_path: str = "") -> dict:
    """
    Generate complete benchmark.json from run results.
    """
    results = load_run_results(benchmark_dir)
    run_summary = aggregate_results(results)

    # Build runs array for benchmark.json
    runs = []
    for config in results:
        for result in results[config]:
            runs.append(
                {
                    "eval_id": result["eval_id"],
                    "eval_name": result["eval_name"],
                    "configuration": config,
                    "run_number": result["run_number"],
                    "result": {
                        "pass_rate": result["pass_rate"],
                        "passed": result["passed"],
                        "failed": result["failed"],
                        "total": result["total"],
                        "time_seconds": result["time_seconds"],
                        "tokens": result.get("tokens"),
                        "tool_calls": result.get("tool_calls"),
                        "errors": result.get("errors"),
                    },
                    "grading_provenance": result["grading_provenance"],
                    "expectations": result["expectations"],
                    "notes": result["notes"],
                }
            )

    # Determine eval IDs from results
    eval_ids = sorted(set(r["eval_id"] for config in results.values() for r in config))

    # Report the runs actually found, not an assumed count — with one run per eval
    # the run count is the only thing that says whether a spread is measurable.
    runs_per_configuration = max(
        (
            sum(1 for r in runs if r["eval_id"] == eval_id)
            for runs in results.values()
            for eval_id in {r["eval_id"] for r in runs}
        ),
        default=0,
    )

    benchmark = {
        "metadata": {
            "skill_name": skill_name or "<skill-name>",
            "skill_path": skill_path or "<path/to/skill>",
            "executor_model": "<model-name>",
            "analyzer_model": "<model-name>",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evals_run": eval_ids,
            "runs_per_configuration": runs_per_configuration,
        },
        "runs": runs,
        "run_summary": run_summary,
        "notes": [],  # To be filled by the analyst pass
    }

    return benchmark


def generate_markdown(benchmark: dict) -> str:
    """Generate human-readable benchmark.md from benchmark data."""
    metadata = benchmark["metadata"]
    run_summary = benchmark["run_summary"]

    # One column per configuration found, and a Delta column only when a delta was
    # computed — an empty second column would invent a baseline that wasn't run.
    configs = [k for k in run_summary if k != "delta"] or ["with_skill"]
    delta = run_summary.get("delta")

    n_runs = metadata["runs_per_configuration"]
    run_word = "run" if n_runs == 1 else "runs"

    header = ["Metric"] + [c.replace("_", " ").title() for c in configs]
    if delta:
        header.append("Delta")

    lines = [
        f"# Skill Benchmark: {metadata['skill_name']}",
        "",
        f"**Model**: {metadata['executor_model']}",
        f"**Date**: {metadata['timestamp']}",
        f"**Evals**: {', '.join(map(str, metadata['evals_run']))} ({n_runs} {run_word} each per configuration)",
        "",
        "## Summary",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join("-" * (len(h) + 2) for h in header) + "|",
    ]

    def append_row(label, key, fmt, delta_suffix=""):
        cells = [fmt(run_summary.get(c, {}).get(key, {})) for c in configs]
        if delta:
            delta_value = delta.get(key)
            cells.append("—" if delta_value is None else f"{delta_value}{delta_suffix}")
        lines.append("| " + " | ".join([label] + cells) + " |")

    def format_stat(stats: dict, *, scale: float = 1.0, suffix: str = "", digits: int = 1) -> str:
        mean = stats.get("mean")
        if mean is None:
            return "UNAVAILABLE"
        mean_text = f"{mean * scale:.{digits}f}{suffix}"
        repeat = stats.get("repeat_stddev")
        cross = stats.get("cross_eval_stddev")
        repeat_text = "unavailable" if repeat is None else f"{repeat * scale:.{digits}f}{suffix}"
        cross_text = "unavailable" if cross is None else f"{cross * scale:.{digits}f}{suffix}"
        return f"{mean_text} (repeat σ {repeat_text}; cross-eval σ {cross_text})"

    append_row("Pass Rate", "pass_rate", lambda s: format_stat(s, scale=100, suffix="%", digits=0))
    append_row("Time", "time_seconds", lambda s: format_stat(s, suffix="s", digits=1), "s")
    append_row("Tokens", "tokens", lambda s: format_stat(s, digits=0))

    # Notes section
    if benchmark.get("notes"):
        lines.extend(["", "## Notes", ""])
        for note in benchmark["notes"]:
            lines.append(f"- {note}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Aggregate benchmark run results into summary statistics")
    parser.add_argument("benchmark_dir", type=Path, help="Path to the benchmark directory")
    parser.add_argument("--skill-name", default="", help="Name of the skill being benchmarked")
    parser.add_argument("--skill-path", default="", help="Path to the skill being benchmarked")
    parser.add_argument(
        "--output", "-o", type=Path, help="Output path for benchmark.json (default: <benchmark_dir>/benchmark.json)"
    )

    args = parser.parse_args()

    if not args.benchmark_dir.exists():
        print(f"Directory not found: {args.benchmark_dir}")
        sys.exit(1)

    # Generate benchmark
    benchmark = generate_benchmark(args.benchmark_dir, args.skill_name, args.skill_path)

    # Determine output paths
    output_json = args.output or (args.benchmark_dir / "benchmark.json")
    output_md = output_json.with_suffix(".md")

    # Write benchmark.json
    with open(output_json, "w") as f:
        json.dump(benchmark, f, indent=2)
    print(f"Generated: {output_json}")

    # Write benchmark.md
    markdown = generate_markdown(benchmark)
    with open(output_md, "w") as f:
        f.write(markdown)
    print(f"Generated: {output_md}")

    # Print summary
    run_summary = benchmark["run_summary"]
    configs = [k for k in run_summary if k != "delta"]
    delta = run_summary.get("delta")

    print("\nSummary:")
    for config in configs:
        pr = run_summary[config]["pass_rate"]["mean"]
        label = config.replace("_", " ").title()
        value = f"{pr * 100:.1f}%" if pr is not None else "UNAVAILABLE"
        print(f"  {label}: {value} pass rate")
    if delta:
        print(f"  Delta:         {delta['pass_rate']}")


if __name__ == "__main__":
    main()
