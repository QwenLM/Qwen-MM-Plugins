#!/usr/bin/env python3
"""Run trigger evaluation for a skill description.

Tests whether a skill's description causes an agent to trigger (read the skill)
for a set of queries, by asking an LLM (via an OpenAI-compatible API) to judge
whether each query should invoke the skill.
"""

import argparse
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from shared.api_openai import resolve_openai_endpoint


def _load_parse_skill_md():
    """Load the sibling helper without depending on the global ``scripts`` package name."""
    if __package__:
        from .utils import parse_skill_md as parser

        return parser

    utils_path = Path(__file__).resolve().with_name("utils.py")
    spec = importlib.util.spec_from_file_location("_omni_skill_creator_run_eval_utils", utils_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load run_eval utilities from {utils_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_skill_md


parse_skill_md = _load_parse_skill_md()

# ---------------------------------------------------------------------------
# Trigger evaluator
# ---------------------------------------------------------------------------


class TriggerEvaluator:
    """Use an LLM (via OpenAI-compatible API) to judge trigger likelihood.

    Sends the skill description + query to the model and asks for a yes/no
    judgment. Stateless per call, so a single instance is safe to share
    across threads.
    """

    TRIGGER_PROMPT = """\
You are deciding whether a user's query should activate a specific agent skill.

A "skill" is a specialized instruction set that an AI coding assistant can invoke. \
The assistant sees only the skill's name and description when deciding whether to \
use it. If the query matches the skill's purpose, the assistant should trigger it.

Skill name: {skill_name}
Skill description:
{skill_description}

User query: "{query}"

Would a capable AI coding assistant choose to invoke this skill for the above query? \
Consider both explicit matches and reasonable intent overlap.

Answer with ONLY "yes" or "no"."""

    def __init__(
        self,
        model: str = "qwen-plus",
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: int = 30,
    ):
        self.model = model
        self.api_base, self.api_key = resolve_openai_endpoint({"base_url": api_base, "api_key": api_key})
        self.timeout = timeout

    def check_trigger(self, query: str, skill_name: str, skill_description: str) -> bool:
        from openai import OpenAI

        client = OpenAI(base_url=self.api_base, api_key=self.api_key, timeout=self.timeout)
        prompt = self.TRIGGER_PROMPT.format(
            skill_name=skill_name,
            skill_description=skill_description,
            query=query,
        )
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8,
            temperature=0.0,
        )
        answer = resp.choices[0].message.content.strip().lower()
        return answer.startswith("yes")


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------


def _check_one(evaluator: TriggerEvaluator, query: str, skill_name: str, skill_description: str) -> bool:
    return evaluator.check_trigger(query, skill_name, skill_description)


def run_eval(
    eval_set: list[dict],
    skill_name: str,
    description: str,
    evaluator: TriggerEvaluator,
    num_workers: int = 10,
    runs_per_query: int = 1,
    trigger_threshold: float = 0.5,
) -> dict:
    """Run the full eval set and return results."""
    # The evaluator makes stateless HTTP calls, so a single instance is shared
    # across threads.
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_to_info = {}
        for item in eval_set:
            for run_idx in range(runs_per_query):
                future = executor.submit(
                    _check_one,
                    evaluator,
                    item["query"],
                    skill_name,
                    description,
                )
                future_to_info[future] = (item, run_idx)

        query_triggers: dict[str, list[bool]] = {}
        query_items: dict[str, dict] = {}
        for future in as_completed(future_to_info):
            item, _ = future_to_info[future]
            query = item["query"]
            query_items[query] = item
            if query not in query_triggers:
                query_triggers[query] = []
            try:
                query_triggers[query].append(future.result())
            except Exception as e:
                print(f"Warning: query failed: {e}", file=sys.stderr)
                query_triggers[query].append(False)

    results = []
    for query, triggers in query_triggers.items():
        item = query_items[query]
        trigger_rate = sum(triggers) / len(triggers)
        should_trigger = item["should_trigger"]
        if should_trigger:
            did_pass = trigger_rate >= trigger_threshold
        else:
            did_pass = trigger_rate < trigger_threshold
        results.append(
            {
                "query": query,
                "should_trigger": should_trigger,
                "trigger_rate": trigger_rate,
                "triggers": sum(triggers),
                "runs": len(triggers),
                "pass": did_pass,
            }
        )

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run trigger evaluation for a skill description")
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--description", default=None, help="Override description to test")
    parser.add_argument("--num-workers", type=int, default=10, help="Number of parallel workers")
    parser.add_argument("--runs-per-query", type=int, default=3, help="Number of runs per query")
    parser.add_argument("--trigger-threshold", type=float, default=0.5, help="Trigger rate threshold")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")

    parser.add_argument("--model", default=None, help="Model name for the OpenAI-compatible API")
    parser.add_argument("--api-base", default=None, help="API base URL (defaults to DASHSCOPE_BASE_URL)")
    parser.add_argument(
        "--api-key", default=None, help="API key override (otherwise selected for the effective endpoint)"
    )
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per query in seconds")
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)

    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    name, original_description, content = parse_skill_md(skill_path)
    description = args.description or original_description

    # Build evaluator
    evaluator_kwargs: dict = {}
    if args.model:
        evaluator_kwargs["model"] = args.model
    if args.timeout:
        evaluator_kwargs["timeout"] = args.timeout
    if args.api_base:
        evaluator_kwargs["api_base"] = args.api_base
    if args.api_key:
        evaluator_kwargs["api_key"] = args.api_key

    evaluator = TriggerEvaluator(**evaluator_kwargs)

    if args.verbose:
        print(f"Evaluating: {description}", file=sys.stderr)

    output = run_eval(
        eval_set=eval_set,
        skill_name=name,
        description=description,
        evaluator=evaluator,
        num_workers=args.num_workers,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
    )

    if args.verbose:
        summary = output["summary"]
        print(f"Results: {summary['passed']}/{summary['total']} passed", file=sys.stderr)
        for r in output["results"]:
            status = "PASS" if r["pass"] else "FAIL"
            rate_str = f"{r['triggers']}/{r['runs']}"
            print(f"  [{status}] rate={rate_str} expected={r['should_trigger']}: {r['query'][:70]}", file=sys.stderr)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
