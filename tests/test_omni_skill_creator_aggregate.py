"""Behavioral tests for the release L3 benchmark aggregation semantics."""

import importlib.util
import json
from pathlib import Path

from conftest import REPO_ROOT

_SCRIPT = Path(REPO_ROOT) / "src/capabilities/omni-skill-creator/skill/scripts/aggregate_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("aggregate_benchmark", _SCRIPT)
aggregate_benchmark = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(aggregate_benchmark)


def _write_run(
    root: Path,
    eval_id: int,
    run_number: int,
    pass_rate: float,
    *,
    tokens: int | None,
    seconds: float,
    provenance: dict | None = None,
) -> None:
    eval_dir = root / f"eval-{eval_id}-fixture-{eval_id}"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "eval_metadata.json").write_text(json.dumps({"eval_id": eval_id, "eval_name": f"fixture-{eval_id}"}))
    run_dir = eval_dir / "with_skill" / f"run-{run_number}"
    run_dir.mkdir(parents=True)
    grading = {
        "summary": {
            "pass_rate": pass_rate,
            "passed": round(pass_rate * 10),
            "failed": 10 - round(pass_rate * 10),
            "total": 10,
        },
        # Character count is not a token measurement and must never be used as one.
        "execution_metrics": {"output_chars": 9999, "total_tool_calls": 3},
        "expectations": [],
    }
    if provenance is not None:
        grading["grading_provenance"] = provenance
    (run_dir / "grading.json").write_text(json.dumps(grading))
    timing = {"total_duration_seconds": seconds}
    if tokens is not None:
        timing["total_tokens"] = tokens
    (run_dir / "timing.json").write_text(json.dumps(timing))


def test_single_runs_separate_cross_eval_spread_and_keep_missing_tokens_unknown(tmp_path):
    provenance = {"mode": "independent_subagent", "blind_to_configuration": True}
    _write_run(tmp_path, 1, 1, 0.8, tokens=100, seconds=10, provenance=provenance)
    _write_run(tmp_path, 2, 1, 0.6, tokens=None, seconds=20, provenance=provenance)

    benchmark = aggregate_benchmark.generate_benchmark(tmp_path)

    pass_stats = benchmark["run_summary"]["with_skill"]["pass_rate"]
    assert pass_stats["mean"] == 0.7
    assert pass_stats["repeat_stddev"] is None
    assert pass_stats["cross_eval_stddev"] == 0.1414
    assert benchmark["run_summary"]["with_skill"]["tokens"]["mean"] == 100.0
    assert benchmark["run_summary"]["with_skill"]["tokens"]["n_runs"] == 1
    assert benchmark["runs"][0]["grading_provenance"] == provenance


def test_repeat_variance_is_not_confused_with_cross_eval_dispersion(tmp_path):
    provenance = {"mode": "independent_subagent", "blind_to_configuration": True}
    _write_run(tmp_path, 1, 1, 0.8, tokens=100, seconds=10, provenance=provenance)
    _write_run(tmp_path, 1, 2, 1.0, tokens=120, seconds=12, provenance=provenance)

    benchmark = aggregate_benchmark.generate_benchmark(tmp_path)
    stats = benchmark["run_summary"]["with_skill"]["pass_rate"]

    assert stats["mean"] == 0.9
    assert stats["repeat_stddev"] == 0.1414
    assert stats["cross_eval_stddev"] is None


def test_missing_provenance_remains_unknown(tmp_path, capsys):
    _write_run(tmp_path, 1, 1, 1.0, tokens=None, seconds=10)

    benchmark = aggregate_benchmark.generate_benchmark(tmp_path)

    assert benchmark["runs"][0]["grading_provenance"] is None
    assert "independence is UNKNOWN" in capsys.readouterr().out
    assert benchmark["run_summary"]["with_skill"]["tokens"]["mean"] is None
    assert "UNAVAILABLE" in aggregate_benchmark.generate_markdown(benchmark)
