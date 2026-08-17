"""Tests for the reusable subprocess-isolation protocol."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from shared.isolated_worker import IsolatedWorkerError, run_isolated

_TARGETS = """
import os
import time

def echo(arguments):
    print("worker log must not reach MCP stdout")
    return arguments

def environment(arguments):
    return os.environ.get(arguments["name"])

def fail(arguments):
    print("diagnostic from child")
    raise RuntimeError(arguments["message"])

def hang(arguments):
    time.sleep(arguments["seconds"])
    return "late"

def exit_without_result(arguments):
    os._exit(0)

def crash(arguments):
    print("crash diagnostic", flush=True)
    os._exit(arguments["code"])

def large(arguments):
    return "x" * arguments["size"]
"""


@pytest.fixture
def isolated_target(tmp_path: Path, repo_root: str) -> tuple[str, dict[str, str]]:
    module_name = "isolated_worker_target"
    (tmp_path / f"{module_name}.py").write_text(_TARGETS, encoding="utf-8")
    python_path = os.pathsep.join(
        part for part in [str(tmp_path), str(Path(repo_root) / "src"), os.environ.get("PYTHONPATH", "")] if part
    )
    return module_name, {"PYTHONPATH": python_path}


def test_isolated_worker_round_trip_and_stdout_capture(isolated_target, capsys):
    module, env = isolated_target
    assert run_isolated(module, "echo", {"value": "ok"}, env_overrides=env) == {"value": "ok"}
    assert capsys.readouterr().out == ""


def test_isolated_worker_environment_override_and_removal(isolated_target, monkeypatch):
    module, env = isolated_target
    monkeypatch.setenv("ISOLATED_TEST_FLAG", "parent")
    assert (
        run_isolated(
            module,
            "environment",
            {"name": "ISOLATED_TEST_FLAG"},
            env_overrides={**env, "ISOLATED_TEST_FLAG": "child"},
        )
        == "child"
    )
    assert (
        run_isolated(
            module,
            "environment",
            {"name": "ISOLATED_TEST_FLAG"},
            env_overrides={**env, "ISOLATED_TEST_FLAG": None},
        )
        is None
    )


def test_isolated_worker_returns_structured_error(isolated_target):
    module, env = isolated_target
    with pytest.raises(IsolatedWorkerError, match="RuntimeError: expected failure"):
        run_isolated(module, "fail", {"message": "expected failure"}, env_overrides=env)


def test_isolated_worker_timeout(isolated_target):
    module, env = isolated_target
    with pytest.raises(IsolatedWorkerError, match="timed out"):
        run_isolated(module, "hang", {"seconds": 5}, timeout=0.1, env_overrides=env)


def test_isolated_worker_rejects_missing_result(isolated_target):
    module, env = isolated_target
    with pytest.raises(IsolatedWorkerError, match="produced no result"):
        run_isolated(module, "exit_without_result", {}, env_overrides=env)


def test_isolated_worker_reports_process_crash_and_log_tail(isolated_target):
    module, env = isolated_target
    with pytest.raises(IsolatedWorkerError, match=r"exited with code 7: crash diagnostic"):
        run_isolated(module, "crash", {"code": 7}, env_overrides=env)


def test_isolated_worker_enforces_result_limit(isolated_target):
    module, env = isolated_target
    with pytest.raises(IsolatedWorkerError, match="result is too large"):
        run_isolated(module, "large", {"size": 4096}, max_result_bytes=128, env_overrides=env)


def test_isolated_worker_rejects_non_json_arguments(isolated_target):
    module, env = isolated_target
    with pytest.raises(ValueError, match="not JSON-serializable"):
        run_isolated(module, "echo", {"bad": object()}, env_overrides=env)


def test_isolated_worker_supports_concurrent_calls(isolated_target):
    module, env = isolated_target
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda value: run_isolated(module, "echo", {"value": value}, env_overrides=env),
                range(3),
            )
        )
    assert results == [{"value": 0}, {"value": 1}, {"value": 2}]


def test_isolated_worker_cleans_temporary_directory(isolated_target, tmp_path, monkeypatch):
    from shared import isolated_worker

    module, env = isolated_target
    cleanup_root = tmp_path / "worker-temp"
    cleanup_root.mkdir()
    monkeypatch.setattr(isolated_worker.tempfile, "tempdir", str(cleanup_root))

    assert run_isolated(module, "echo", {"value": "ok"}, env_overrides=env) == {"value": "ok"}
    assert list(cleanup_root.iterdir()) == []
