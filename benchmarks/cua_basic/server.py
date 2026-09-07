#!/usr/bin/env python3
"""Zero-dependency local web fixture and verifier for CUA ablations."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import secrets
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
FIXTURE_PATH = ROOT / "fixtures" / "benchmark-upload.txt"
CASE_CATALOG = json.loads((ROOT / "cases.json").read_text())
CASES = {item["id"]: item for item in CASE_CATALOG}

COLORS = ["blue", "orange", "green", "purple"]
SHAPES = ["circle", "triangle", "square", "diamond"]
MARKS = ["dot", "stripe", "cross"]
ROLES = ["Editor", "Reviewer", "Analyst"]
TIMEZONES = ["Asia/Shanghai", "Europe/London", "America/Los_Angeles"]
COLUMNS = ["Todo", "In progress", "Done"]


def _upload_metadata() -> dict[str, Any]:
    content = FIXTURE_PATH.read_bytes()
    return {
        "name": FIXTURE_PATH.name,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _make_config(case_id: str, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    if case_id == "01":
        combinations = [(color, shape, mark) for color in COLORS for shape in SHAPES for mark in MARKS]
        rng.shuffle(combinations)
        cells = [{"color": color, "shape": shape, "mark": mark} for color, shape, mark in combinations[:24]]
        target_cell = rng.randrange(len(cells))
        target = cells[target_cell]
        return {"cells": cells, "target_cell": target_cell, "target": target}
    if case_id == "02":
        return {"target_panel": rng.choice(["Billing", "Notifications"])}
    if case_id == "03":
        orders = [f"ORD-{number}" for number in range(2038, 2050)]
        rng.shuffle(orders)
        return {"orders": orders, "target_order": rng.choice(orders)}
    if case_id == "04":
        suffix = seed % 1000
        return {
            "user": {
                "name": f"Mei Chen {suffix:03d}",
                "email": f"mei.chen.{suffix:03d}@example.test",
                "role": rng.choice(ROLES),
                "send_invite": bool(rng.randrange(2)),
            }
        }
    if case_id == "05":
        return {
            "preferences": {
                "timezone": rng.choice(TIMEZONES),
                "email_notifications": bool(rng.randrange(2)),
                "compact_mode": bool(rng.randrange(2)),
            }
        }
    if case_id == "06":
        return {"target_item": f"INV-{70 + seed % 25:03d}"}
    if case_id == "07":
        cards = ["Fix login bug", "Review release notes", "Update API examples"]
        target_card = rng.choice(cards)
        target_column = rng.choice(["In progress", "Done"])
        return {"cards": cards, "target_card": target_card, "target_column": target_column}
    if case_id == "08":
        return {"dialog_choice": rng.choice(["Confirm", "Cancel"]), "delay_ms": 900}
    if case_id == "09":
        return {"code": f"{rng.randrange(100000, 1000000)}"}
    if case_id == "10":
        upload = _upload_metadata()
        output = f"CUA-BASIC-10\nseed={seed}\nstatus=processed\n".encode()
        return {
            "upload": upload,
            "delay_ms": 900,
            "download_name": f"cua-basic-result-{seed}.txt",
            "download_sha256": hashlib.sha256(output).hexdigest(),
            "download_content": output.decode(),
        }
    raise KeyError(case_id)


def _task_prompt(case_id: str, config: dict[str, Any]) -> str:
    if case_id == "01":
        target = config["target"]
        return f"Click the {target['color']} {target['shape']} with a {target['mark']}."
    if case_id == "02":
        return f"Press Enable in the {config['target_panel']} panel. Do not change the other panel."
    if case_id == "03":
        return f"Search for order {config['target_order']} and open that exact order."
    if case_id == "04":
        user = config["user"]
        invite = "enabled" if user["send_invite"] else "disabled"
        return f"Create user {user['name']} with email {user['email']}, role {user['role']}, and Send invite {invite}."
    if case_id == "05":
        prefs = config["preferences"]
        email = "on" if prefs["email_notifications"] else "off"
        compact = "on" if prefs["compact_mode"] else "off"
        return (
            f"Set timezone to {prefs['timezone']}, Email notifications {email}, "
            f"and Compact mode {compact}, then save preferences."
        )
    if case_id == "06":
        return f"Find and select {config['target_item']} in the invoice list."
    if case_id == "07":
        return f"Drag {config['target_card']} to the {config['target_column']} column."
    if case_id == "08":
        return (
            "Start the operation exactly once, wait for its confirmation dialog, then choose "
            f"{config['dialog_choice']}."
        )
    if case_id == "09":
        return "Open the Access code link in its new tab, read the code, return here, and submit it."
    if case_id == "10":
        return (
            f"Upload {config['upload']['name']}, start processing, wait for completion, "
            "then download the generated result."
        )
    raise KeyError(case_id)


@dataclass
class Run:
    id: str
    case_id: str
    seed: int
    config: dict[str, Any]
    task: str
    created_at: float = field(default_factory=time.time)
    state: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


class BenchmarkStore:
    """Thread-safe in-memory benchmark state and deterministic verifier."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}

    def create_run(self, case_id: str, seed: int = 0) -> dict[str, Any]:
        case_id = str(case_id).zfill(2)
        if case_id not in CASES:
            raise KeyError(f"unknown case: {case_id}")
        config = _make_config(case_id, int(seed))
        run = Run(
            id=secrets.token_hex(8),
            case_id=case_id,
            seed=int(seed),
            config=config,
            task=_task_prompt(case_id, config),
        )
        with self._lock:
            self._runs[run.id] = run
        return self.public_run(run)

    def get_run(self, run_id: str) -> Run:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        return run

    def public_run(self, run: Run) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "case_id": run.case_id,
            "seed": run.seed,
            "title": CASES[run.case_id]["title"],
            "category": CASES[run.case_id]["category"],
            "max_actions": CASES[run.case_id]["max_actions"],
            "task": run.task,
            "url": f"/case/{run.case_id}?run_id={run.id}",
            "agent_prompt": (
                f"A browser window is already open at {{BASE_URL}}/case/{run.case_id}?run_id={run.id}. "
                f"Complete this task using only the available CUA tools: {run.task} "
                "Stop after the page reports that the case passed."
            ),
        }

    def view(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        return {**self.public_run(run), "config": run.config, "result": self.result(run_id)}

    def record_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(f"unknown run: {run_id}")
            run.events.append({"type": event_type, "payload": payload, "at": time.time()})
            state = run.state
            case_id = run.case_id
            if case_id == "01" and event_type == "select_cell":
                state["selected_cell"] = int(payload.get("cell", -1))
            elif case_id == "02" and event_type == "enable_panel":
                panels = state.setdefault("enabled_panels", [])
                panel = str(payload.get("panel", ""))
                if panel not in panels:
                    panels.append(panel)
            elif case_id == "03" and event_type == "open_order":
                state["opened_order"] = str(payload.get("order", ""))
            elif case_id == "04" and event_type == "submit_user":
                state["created_user"] = {
                    "name": str(payload.get("name", "")),
                    "email": str(payload.get("email", "")),
                    "role": str(payload.get("role", "")),
                    "send_invite": bool(payload.get("send_invite", False)),
                }
            elif case_id == "05" and event_type == "save_preferences":
                state["preferences"] = {
                    "timezone": str(payload.get("timezone", "")),
                    "email_notifications": bool(payload.get("email_notifications", False)),
                    "compact_mode": bool(payload.get("compact_mode", False)),
                }
            elif case_id == "06" and event_type == "select_item":
                state["selected_item"] = str(payload.get("item", ""))
            elif case_id == "07" and event_type == "move_card":
                columns = state.setdefault("card_columns", {card: "Todo" for card in run.config["cards"]})
                columns[str(payload.get("card", ""))] = str(payload.get("column", ""))
            elif case_id == "08" and event_type == "start":
                state["start_count"] = int(state.get("start_count", 0)) + 1
            elif case_id == "08" and event_type == "dialog_choice":
                state["dialog_choice"] = str(payload.get("choice", ""))
            elif case_id == "09" and event_type == "submit_code":
                state["submitted_code"] = str(payload.get("code", ""))
            elif case_id == "10" and event_type == "upload":
                state["upload"] = {
                    "name": str(payload.get("name", "")),
                    "size": int(payload.get("size", -1)),
                    "sha256": str(payload.get("sha256", "")),
                }
            elif case_id == "10" and event_type == "process":
                state["processed"] = True
            elif case_id == "10" and event_type == "download":
                state["download_requested"] = True
        return self.result(run_id)

    def result(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        config = run.config
        state = dict(run.state)
        case_id = run.case_id
        if case_id == "01":
            passed = state.get("selected_cell") == config["target_cell"]
        elif case_id == "02":
            passed = state.get("enabled_panels") == [config["target_panel"]]
        elif case_id == "03":
            passed = state.get("opened_order") == config["target_order"]
        elif case_id == "04":
            passed = state.get("created_user") == config["user"]
        elif case_id == "05":
            passed = state.get("preferences") == config["preferences"]
        elif case_id == "06":
            passed = state.get("selected_item") == config["target_item"]
        elif case_id == "07":
            expected_columns = {card: "Todo" for card in config["cards"]}
            expected_columns[config["target_card"]] = config["target_column"]
            passed = state.get("card_columns") == expected_columns
        elif case_id == "08":
            passed = state.get("start_count") == 1 and state.get("dialog_choice") == config["dialog_choice"]
        elif case_id == "09":
            passed = state.get("submitted_code") == config["code"]
        elif case_id == "10":
            passed = (
                state.get("upload") == config["upload"]
                and state.get("processed") is True
                and state.get("download_requested") is True
            )
        else:  # pragma: no cover - catalog guards this
            passed = False
        return {
            "run_id": run.id,
            "case_id": case_id,
            "passed": passed,
            "event_count": len(run.events),
            "state": state,
        }

    def download(self, run_id: str) -> tuple[str, bytes]:
        run = self.get_run(run_id)
        if run.case_id != "10":
            raise ValueError("downloads are available only for case 10")
        self.record_event(run_id, "download")
        return run.config["download_name"], run.config["download_content"].encode()


class BenchmarkHandler(BaseHTTPRequestHandler):
    server_version = "CUABasic/1.0"

    @property
    def store(self) -> BenchmarkStore:
        return self.server.store  # type: ignore[attr-defined,no-any-return]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _json(self, status: HTTPStatus, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/cases":
                self._json(HTTPStatus.OK, CASE_CATALOG)
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] in {"view", "result"}:
                run_id = parts[2]
                data = self.store.view(run_id) if parts[3] == "view" else self.store.result(run_id)
                self._json(HTTPStatus.OK, data)
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "download":
                name, body = self.store.download(parts[2])
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path in {"/", "/index.html"} or path.startswith("/case/"):
                self._file(STATIC_ROOT / "index.html", "text/html; charset=utf-8")
                return
            if path == "/app.css":
                self._file(STATIC_ROOT / "app.css", "text/css; charset=utf-8")
                return
            if path == "/app.js":
                self._file(STATIC_ROOT / "app.js", "text/javascript; charset=utf-8")
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except KeyError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (OSError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/runs":
                body = self._body()
                run = self.store.create_run(str(body.get("case_id", "")), int(body.get("seed", 0)))
                host = self.headers.get("Host", "127.0.0.1")
                run["url"] = f"http://{host}{run['url']}"
                run["agent_prompt"] = run["agent_prompt"].replace("{BASE_URL}", f"http://{host}")
                self._json(HTTPStatus.CREATED, run)
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "events":
                body = self._body()
                event_type = str(body.get("type", ""))
                if not event_type:
                    raise ValueError("event type is required")
                payload = body.get("payload", {})
                if not isinstance(payload, dict):
                    raise ValueError("event payload must be an object")
                self._json(HTTPStatus.OK, self.store.record_event(parts[2], event_type, payload))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except KeyError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


class BenchmarkHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: BenchmarkStore | None = None) -> None:
        self.store = store or BenchmarkStore()
        super().__init__(address, BenchmarkHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = BenchmarkHTTPServer((args.host, args.port))
    print(f"CUA Basic listening on http://{args.host}:{args.port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
