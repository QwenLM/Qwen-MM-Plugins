"""Run JSON-serializable callables in a fresh Python interpreter.

The parent process never uses the child's stdin/stdout for the RPC payload: those
streams may belong to an MCP transport or be written by native libraries. Requests
and results use private files, while child output is captured in a bounded log tail.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Mapping

DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_LOG_TAIL_BYTES = 8 * 1024
MAX_TRACEBACK_CHARS = 8 * 1024


class IsolatedWorkerError(RuntimeError):
    """An isolated call failed before returning a valid result."""


def _read_tail(path: Path, limit: int = MAX_LOG_TAIL_BYTES) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_json(path: Path, max_bytes: int) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise IsolatedWorkerError(f"isolated worker produced no result: {exc}") from exc
    if size > max_bytes:
        raise IsolatedWorkerError(f"isolated worker result is too large ({size} bytes; limit {max_bytes})")
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolatedWorkerError(f"isolated worker produced an invalid result: {exc}") from exc


def run_isolated(
    module: str,
    function: str,
    arguments: Mapping[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    env_overrides: Mapping[str, str | None] | None = None,
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
) -> Any:
    """Call ``module.function(dict(arguments))`` in a fresh interpreter.

    The callable and its result must be JSON-serializable. Child stdout/stderr are
    captured separately so they cannot corrupt the parent process's MCP transport.
    """

    if not module or not function:
        raise ValueError("module and function must be non-empty")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_result_bytes <= 0:
        raise ValueError("max_result_bytes must be positive")

    request = {"module": module, "function": function, "arguments": dict(arguments)}
    try:
        encoded_request = json.dumps(request, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"isolated worker arguments are not JSON-serializable: {exc}") from exc
    if len(encoded_request) > MAX_REQUEST_BYTES:
        raise ValueError(f"isolated worker request is too large ({len(encoded_request)} bytes)")

    with tempfile.TemporaryDirectory(prefix="qwen_mm_isolated_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        request_path = tmp_path / "request.json"
        result_path = tmp_path / "result.json"
        log_path = tmp_path / "worker.log"
        request_path.write_bytes(encoded_request)

        worker_env = os.environ.copy()
        for key, value in (env_overrides or {}).items():
            if value is None:
                worker_env.pop(key, None)
            else:
                worker_env[key] = value

        command = [
            sys.executable,
            "-m",
            "shared.isolated_worker",
            str(request_path),
            str(result_path),
        ]
        try:
            with log_path.open("wb") as log_file:
                completed = subprocess.run(
                    command,
                    close_fds=True,
                    env=worker_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired as exc:
            log_tail = _read_tail(log_path)
            detail = f"; output: {log_tail}" if log_tail else ""
            raise IsolatedWorkerError(
                f"isolated call {module}.{function} timed out after {timeout:g}s{detail}"
            ) from exc
        except OSError as exc:
            raise IsolatedWorkerError(f"failed to start isolated call {module}.{function}: {exc}") from exc

        if completed.returncode != 0:
            log_tail = _read_tail(log_path)
            detail = f": {log_tail}" if log_tail else ""
            raise IsolatedWorkerError(
                f"isolated call {module}.{function} exited with code {completed.returncode}{detail}"
            )

        envelope = _read_json(result_path, max_result_bytes)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
            raise IsolatedWorkerError("isolated worker result has an invalid envelope")
        if not envelope["ok"]:
            error = envelope.get("error")
            if isinstance(error, dict):
                error_type = error.get("type") or "Error"
                message = error.get("message") or "isolated call failed"
                raise IsolatedWorkerError(f"{error_type}: {message}")
            raise IsolatedWorkerError("isolated call failed")
        if "result" not in envelope:
            raise IsolatedWorkerError("isolated worker result is missing 'result'")
        return envelope["result"]


def _write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as stream:
            json.dump(envelope, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _worker_main(request_path: Path, result_path: Path) -> int:
    try:
        request = _read_json(request_path, MAX_REQUEST_BYTES)
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        module_name = request.get("module")
        function_name = request.get("function")
        arguments = request.get("arguments")
        if not isinstance(module_name, str) or not isinstance(function_name, str):
            raise ValueError("request module and function must be strings")
        if not isinstance(arguments, dict):
            raise ValueError("request arguments must be an object")

        module = importlib.import_module(module_name)
        target = getattr(module, function_name)
        if not callable(target):
            raise TypeError(f"{module_name}.{function_name} is not callable")
        result = target(arguments)
        envelope: dict[str, Any] = {"ok": True, "result": result}
        # Validate serialization inside the guarded block so a bad return value
        # becomes a structured worker error instead of a missing result file.
        json.dumps(envelope, ensure_ascii=False)
    except Exception as exc:
        envelope = {
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc()[-MAX_TRACEBACK_CHARS:],
            },
        }

    try:
        _write_envelope(result_path, envelope)
    except Exception as exc:
        print(f"failed to write isolated worker result: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args(argv)
    return _worker_main(args.request, args.result)


if __name__ == "__main__":
    raise SystemExit(main())
