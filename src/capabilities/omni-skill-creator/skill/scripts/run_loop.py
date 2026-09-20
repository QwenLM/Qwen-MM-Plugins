#!/usr/bin/env python3
"""Run the eval + improve loop until all pass or max iterations reached.

Combines run_eval.py and improve_description.py in a loop, tracking history
and returning the best description found. Supports train/test split to prevent
overfitting. Trigger evaluation and description improvement both go through an
OpenAI-compatible API (DashScope, etc.).
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

from scripts.improve_description import improve_description
from scripts.run_eval import TriggerEvaluator, run_eval
from scripts.utils import parse_skill_md


def split_eval_set(eval_set: list[dict], holdout: float, seed: int = 42) -> tuple[list[dict], list[dict]]:
    """Split eval set into train and test sets, stratified by should_trigger."""
    random.seed(seed)

    trigger = [e for e in eval_set if e["should_trigger"]]
    no_trigger = [e for e in eval_set if not e["should_trigger"]]

    random.shuffle(trigger)
    random.shuffle(no_trigger)

    n_trigger_test = max(1, int(len(trigger) * holdout))
    n_no_trigger_test = max(1, int(len(no_trigger) * holdout))

    test_set = trigger[:n_trigger_test] + no_trigger[:n_no_trigger_test]
    train_set = trigger[n_trigger_test:] + no_trigger[n_no_trigger_test:]

    return train_set, test_set


def run_loop(
    eval_set: list[dict],
    skill_path: Path,
    description_override: str | None,
    num_workers: int,
    max_iterations: int,
    runs_per_query: int,
    trigger_threshold: float,
    holdout: float,
    model: str,
    evaluator_kwargs: dict,
    api_base: str | None,
    api_key: str | None,
    verbose: bool,
    log_dir: Path | None = None,
) -> dict:
    """Run the eval + improvement loop."""
    name, original_description, content = parse_skill_md(skill_path)
    current_description = description_override or original_description

    evaluator = TriggerEvaluator(**evaluator_kwargs)

    if holdout > 0:
        train_set, test_set = split_eval_set(eval_set, holdout)
        if verbose:
            print(f"Split: {len(train_set)} train, {len(test_set)} test (holdout={holdout})", file=sys.stderr)
    else:
        train_set = eval_set
        test_set = []

    history = []
    exit_reason = "unknown"

    for iteration in range(1, max_iterations + 1):
        if verbose:
            print(f"\n{'=' * 60}", file=sys.stderr)
            print(f"Iteration {iteration}/{max_iterations}", file=sys.stderr)
            print(f"Description: {current_description}", file=sys.stderr)
            print(f"{'=' * 60}", file=sys.stderr)

        all_queries = train_set + test_set
        t0 = time.time()
        all_results = run_eval(
            eval_set=all_queries,
            skill_name=name,
            description=current_description,
            evaluator=evaluator,
            num_workers=num_workers,
            runs_per_query=runs_per_query,
            trigger_threshold=trigger_threshold,
        )
        eval_elapsed = time.time() - t0

        train_queries_set = {q["query"] for q in train_set}
        train_result_list = [r for r in all_results["results"] if r["query"] in train_queries_set]
        test_result_list = [r for r in all_results["results"] if r["query"] not in train_queries_set]

        train_passed = sum(1 for r in train_result_list if r["pass"])
        train_total = len(train_result_list)
        train_summary = {"passed": train_passed, "failed": train_total - train_passed, "total": train_total}
        train_results = {"results": train_result_list, "summary": train_summary}

        if test_set:
            test_passed = sum(1 for r in test_result_list if r["pass"])
            test_total = len(test_result_list)
            test_summary = {"passed": test_passed, "failed": test_total - test_passed, "total": test_total}
            test_results = {"results": test_result_list, "summary": test_summary}
        else:
            test_results = None
            test_summary = None

        history.append(
            {
                "iteration": iteration,
                "description": current_description,
                "train_passed": train_summary["passed"],
                "train_failed": train_summary["failed"],
                "train_total": train_summary["total"],
                "train_results": train_results["results"],
                "test_passed": test_summary["passed"] if test_summary else None,
                "test_failed": test_summary["failed"] if test_summary else None,
                "test_total": test_summary["total"] if test_summary else None,
                "test_results": test_results["results"] if test_results else None,
                "passed": train_summary["passed"],
                "failed": train_summary["failed"],
                "total": train_summary["total"],
                "results": train_results["results"],
            }
        )

        if verbose:

            def print_eval_stats(label, results, elapsed):
                pos = [r for r in results if r["should_trigger"]]
                neg = [r for r in results if not r["should_trigger"]]
                tp = sum(r["triggers"] for r in pos)
                pos_runs = sum(r["runs"] for r in pos)
                fn = pos_runs - tp
                fp = sum(r["triggers"] for r in neg)
                neg_runs = sum(r["runs"] for r in neg)
                tn = neg_runs - fp
                total = tp + tn + fp + fn
                precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
                recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
                accuracy = (tp + tn) / total if total > 0 else 0.0
                print(
                    f"{label}: {tp + tn}/{total} correct, precision={precision:.0%} recall={recall:.0%} accuracy={accuracy:.0%} ({elapsed:.1f}s)",
                    file=sys.stderr,
                )
                for r in results:
                    status = "PASS" if r["pass"] else "FAIL"
                    rate_str = f"{r['triggers']}/{r['runs']}"
                    print(
                        f"  [{status}] rate={rate_str} expected={r['should_trigger']}: {r['query'][:60]}",
                        file=sys.stderr,
                    )

            print_eval_stats("Train", train_results["results"], eval_elapsed)
            if test_summary:
                print_eval_stats("Test ", test_results["results"], 0)

        if train_summary["failed"] == 0:
            exit_reason = f"all_passed (iteration {iteration})"
            if verbose:
                print(f"\nAll train queries passed on iteration {iteration}!", file=sys.stderr)
            break

        if iteration == max_iterations:
            exit_reason = f"max_iterations ({max_iterations})"
            if verbose:
                print(f"\nMax iterations reached ({max_iterations}).", file=sys.stderr)
            break

        if verbose:
            print("\nImproving description...", file=sys.stderr)

        t0 = time.time()
        blinded_history = [{k: v for k, v in h.items() if not k.startswith("test_")} for h in history]
        new_description = improve_description(
            skill_name=name,
            skill_content=content,
            current_description=current_description,
            eval_results=train_results,
            history=blinded_history,
            model=model,
            api_base=api_base,
            api_key=api_key,
            log_dir=log_dir,
            iteration=iteration,
        )
        improve_elapsed = time.time() - t0

        if verbose:
            print(f"Proposed ({improve_elapsed:.1f}s): {new_description}", file=sys.stderr)

        current_description = new_description

    if test_set:
        best = max(history, key=lambda h: h["test_passed"] or 0)
        best_score = f"{best['test_passed']}/{best['test_total']}"
    else:
        best = max(history, key=lambda h: h["train_passed"])
        best_score = f"{best['train_passed']}/{best['train_total']}"

    if verbose:
        print(f"\nExit reason: {exit_reason}", file=sys.stderr)
        print(f"Best score: {best_score} (iteration {best['iteration']})", file=sys.stderr)

    return {
        "exit_reason": exit_reason,
        "original_description": original_description,
        "best_description": best["description"],
        "best_score": best_score,
        "best_train_score": f"{best['train_passed']}/{best['train_total']}",
        "best_test_score": f"{best['test_passed']}/{best['test_total']}" if test_set else None,
        "final_description": current_description,
        "iterations_run": len(history),
        "holdout": holdout,
        "train_size": len(train_set),
        "test_size": len(test_set),
        "history": history,
    }


def main():
    parser = argparse.ArgumentParser(description="Run eval + improve loop")
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--description", default=None, help="Override starting description")
    parser.add_argument("--num-workers", type=int, default=10, help="Number of parallel workers")
    parser.add_argument("--max-iterations", type=int, default=5, help="Max improvement iterations")
    parser.add_argument("--runs-per-query", type=int, default=3, help="Number of runs per query")
    parser.add_argument("--trigger-threshold", type=float, default=0.5, help="Trigger rate threshold")
    parser.add_argument(
        "--holdout", type=float, default=0.4, help="Fraction of eval set to hold out for testing (0 to disable)"
    )
    parser.add_argument("--model", required=True, help="Model for improvement LLM calls")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")
    parser.add_argument("--results-dir", default=None, help="Save all outputs to a timestamped subdirectory here")

    parser.add_argument("--api-base", default=None, help="API base URL for trigger evaluation and improve calls")
    parser.add_argument("--api-key", default=None, help="API key for trigger evaluation and improve calls")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per trigger-eval query in seconds")
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)

    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    if args.results_dir:
        timestamp = time.strftime("%Y-%m-%d_%H%M%S")
        results_dir = Path(args.results_dir) / timestamp
        results_dir.mkdir(parents=True, exist_ok=True)
    else:
        results_dir = None

    log_dir = results_dir / "logs" if results_dir else None

    # Build evaluator kwargs
    evaluator_kwargs: dict = {}
    if args.model:
        evaluator_kwargs["model"] = args.model
    if args.timeout:
        evaluator_kwargs["timeout"] = args.timeout
    if args.api_base:
        evaluator_kwargs["api_base"] = args.api_base
    if args.api_key:
        evaluator_kwargs["api_key"] = args.api_key

    output = run_loop(
        eval_set=eval_set,
        skill_path=skill_path,
        description_override=args.description,
        num_workers=args.num_workers,
        max_iterations=args.max_iterations,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        holdout=args.holdout,
        model=args.model,
        evaluator_kwargs=evaluator_kwargs,
        api_base=args.api_base,
        api_key=args.api_key,
        verbose=args.verbose,
        log_dir=log_dir,
    )

    json_output = json.dumps(output, indent=2)
    print(json_output)
    if results_dir:
        (results_dir / "results.json").write_text(json_output)

    if results_dir:
        print(f"Results saved to: {results_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
