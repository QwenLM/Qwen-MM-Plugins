from __future__ import annotations

import hashlib
import json
import threading
from urllib.request import Request, urlopen

import pytest
from benchmarks.cua_basic.server import CASES, BenchmarkHTTPServer, BenchmarkStore


def _event_for(run: dict) -> list[tuple[str, dict]]:
    config = run["config"]
    case_id = run["case_id"]
    if case_id == "01":
        return [("select_cell", {"cell": config["target_cell"]})]
    if case_id == "02":
        return [("enable_panel", {"panel": config["target_panel"]})]
    if case_id == "03":
        return [("open_order", {"order": config["target_order"]})]
    if case_id == "04":
        return [("submit_user", config["user"])]
    if case_id == "05":
        return [("save_preferences", config["preferences"])]
    if case_id == "06":
        return [("select_item", {"item": config["target_item"]})]
    if case_id == "07":
        return [("move_card", {"card": config["target_card"], "column": config["target_column"]})]
    if case_id == "08":
        return [("start", {}), ("dialog_choice", {"choice": config["dialog_choice"]})]
    if case_id == "09":
        return [("submit_code", {"code": config["code"]})]
    if case_id == "10":
        return [("upload", config["upload"]), ("process", {}), ("download", {})]
    raise AssertionError(case_id)


def test_catalog_has_ten_unique_cases():
    assert list(CASES) == [f"{index:02d}" for index in range(1, 11)]
    assert len({case["slug"] for case in CASES.values()}) == 10


@pytest.mark.parametrize("case_id", CASES)
def test_each_case_has_a_deterministic_machine_verifier(case_id):
    store = BenchmarkStore()
    created = store.create_run(case_id, seed=42)
    run = store.view(created["run_id"])

    assert run["task"]
    assert run["result"]["passed"] is False

    for event_type, payload in _event_for(run):
        result = store.record_event(run["run_id"], event_type, payload)

    assert result["passed"] is True


def test_semantic_case_rejects_unintended_second_panel_change():
    store = BenchmarkStore()
    run = store.view(store.create_run("02", seed=0)["run_id"])
    target = run["config"]["target_panel"]
    wrong = "Billing" if target == "Notifications" else "Notifications"

    store.record_event(run["run_id"], "enable_panel", {"panel": wrong})
    result = store.record_event(run["run_id"], "enable_panel", {"panel": target})

    assert result["passed"] is False


def test_drag_case_rejects_moving_an_unrequested_card():
    store = BenchmarkStore()
    run = store.view(store.create_run("07", seed=3)["run_id"])
    config = run["config"]
    wrong = next(card for card in config["cards"] if card != config["target_card"])

    store.record_event(run["run_id"], "move_card", {"card": wrong, "column": "Done"})
    result = store.record_event(
        run["run_id"],
        "move_card",
        {"card": config["target_card"], "column": config["target_column"]},
    )

    assert result["passed"] is False


def test_case_ten_download_matches_advertised_checksum():
    store = BenchmarkStore()
    run = store.view(store.create_run("10", seed=9)["run_id"])

    name, content = store.download(run["run_id"])

    assert name == run["config"]["download_name"]
    assert hashlib.sha256(content).hexdigest() == run["config"]["download_sha256"]


def test_http_api_serves_site_and_run_contract():
    server = BenchmarkHTTPServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/", timeout=2) as response:  # noqa: S310 - local test server
            assert b"CUA Basic" in response.read()

        request = Request(
            f"{base_url}/api/runs",
            data=json.dumps({"case_id": "01", "seed": 4}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:  # noqa: S310 - local test server
            created = json.load(response)

        assert created["url"].startswith(base_url)
        assert created["task"] in created["agent_prompt"]
        assert created["run_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
