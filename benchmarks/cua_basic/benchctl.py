#!/usr/bin/env python3
"""Small CLI for creating and inspecting CUA Basic runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(base_url: str, path: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - explicitly local benchmark URL
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"benchmark returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"could not reach {base_url}: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="list the ten case definitions")

    create = subparsers.add_parser("create", help="create a clean seeded run")
    create.add_argument("case_id")
    create.add_argument("--seed", type=int, default=0)
    create.add_argument("--json", action="store_true")

    result = subparsers.add_parser("result", help="read a run's machine verifier")
    result.add_argument("run_id")
    result.add_argument("--json", action="store_true")

    verify_download = subparsers.add_parser("verify-download", help="check a case-10 downloaded artifact")
    verify_download.add_argument("run_id")
    verify_download.add_argument("path", type=Path)

    args = parser.parse_args()
    if args.command == "list":
        cases = _request(args.base_url, "/api/cases")
        for case in cases:
            print(f"{case['id']}  {case['title']:<26} {case['category']}")
        return 0

    if args.command == "create":
        run = _request(args.base_url, "/api/runs", {"case_id": args.case_id, "seed": args.seed})
        if args.json:
            print(json.dumps(run, ensure_ascii=False, indent=2))
        else:
            print(f"RUN_ID={run['run_id']}")
            print(f"URL={run['url']}")
            print(f"TASK={run['task']}")
            print()
            print(run["agent_prompt"])
        return 0

    if args.command == "result":
        result_data = _request(args.base_url, f"/api/runs/{args.run_id}/result")
        if args.json:
            print(json.dumps(result_data, ensure_ascii=False, indent=2))
        else:
            outcome = "PASS" if result_data["passed"] else "NOT PASSED"
            print(f"{outcome} · case {result_data['case_id']} · {result_data['event_count']} recorded events")
            print(json.dumps(result_data["state"], ensure_ascii=False, indent=2))
        return 0 if result_data["passed"] else 1

    view = _request(args.base_url, f"/api/runs/{args.run_id}/view")
    if view["case_id"] != "10":
        raise SystemExit("verify-download applies only to case 10")
    actual = hashlib.sha256(args.path.read_bytes()).hexdigest()
    expected = view["config"]["download_sha256"]
    print(f"actual   {actual}")
    print(f"expected {expected}")
    return 0 if actual == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
