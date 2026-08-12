"""Offline tests for the first-party CUA MCP proxy.

The real runtime has OS permissions and a display dependency, so these tests use a tiny stdio
stand-in. They prove the Qwen proxy resolves open-computer-use without a GUI PATH while owning the
advertised server identity.
"""

from __future__ import annotations

import json
import os
import queue
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from qwen_mm_plugins_cua import proxy


def _executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_resolve_open_computer_use_uses_npx_by_default(monkeypatch):
    monkeypatch.setattr(proxy, "get_env", lambda _: None)

    assert proxy.resolve_open_computer_use(which=lambda name: "/opt/node/bin/npx" if name == "npx" else None) == [
        "/opt/node/bin/npx",
        "--yes",
        f"--package={proxy.OPEN_COMPUTER_USE_PACKAGE}",
        "open-computer-use",
        "mcp",
    ]


def test_resolve_open_computer_use_prefers_explicit_executable(monkeypatch, tmp_path):
    executable = _executable(tmp_path / "open-computer-use")
    monkeypatch.setattr(
        proxy, "get_env", lambda name: str(executable) if name == "QWEN_MM_OPEN_COMPUTER_USE_PATH" else None
    )

    assert proxy.resolve_open_computer_use(which=lambda _: None) == [str(executable), "mcp"]


def test_resolve_open_computer_use_rejects_bad_explicit_path(monkeypatch, tmp_path):
    missing = tmp_path / "missing-open-computer-use"
    monkeypatch.setattr(
        proxy,
        "get_env",
        lambda name: str(missing) if name == "QWEN_MM_OPEN_COMPUTER_USE_PATH" else None,
    )

    with pytest.raises(RuntimeError, match="QWEN_MM_OPEN_COMPUTER_USE_PATH"):
        proxy.resolve_open_computer_use(which=lambda _: None)


def test_resolve_open_computer_use_falls_back_to_path_without_npx(monkeypatch):
    monkeypatch.setattr(proxy, "get_env", lambda _: None)

    assert proxy.resolve_open_computer_use(
        which=lambda name: "/usr/local/bin/open-computer-use" if name == "open-computer-use" else None
    ) == ["/usr/local/bin/open-computer-use", "mcp"]


def test_rewrite_initialize_response_changes_only_server_identity():
    original = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "serverInfo": {"name": "open-computer-use", "version": "0.2.3"},
            "protocolVersion": "2025-06-18",
        },
    }
    rewritten = json.loads(proxy.rewrite_initialize_response(json.dumps(original).encode() + b"\n"))

    assert rewritten["result"]["serverInfo"] == {"name": "qwen-mm-plugins-cua", "version": "0.2.3"}
    assert rewritten["result"]["protocolVersion"] == "2025-06-18"


def test_proxy_forwards_stdio_and_rebrands_initialize(tmp_path):
    fake_runtime = _executable(
        tmp_path / "fake-open-computer-use",
        """#!{python}
import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get('method') == 'initialize':
        print(json.dumps({{'jsonrpc': '2.0', 'id': request['id'], 'result': {{'serverInfo': {{'name': 'open-computer-use', 'version': 'test'}}, 'protocolVersion': '2025-06-18'}}}}), flush=True)
        break
""".format(python=sys.executable),
    )
    env = dict(os.environ, QWEN_MM_OPEN_COMPUTER_USE_PATH=str(fake_runtime))
    process = subprocess.Popen(
        [
            sys.executable,
            "src/capabilities/cua/qwen_mm_plugins_cua",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    output: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
    reader.start()

    try:
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        process.stdin.flush()
        try:
            response_line = output.get(timeout=2)
        except queue.Empty:
            pytest.fail("proxy buffered initialize until stdin closed")
    finally:
        process.stdin.close()
        process.wait(timeout=3)

    assert process.returncode == 0
    response = json.loads(response_line)
    assert response["result"]["serverInfo"]["name"] == "qwen-mm-plugins-cua"
    assert response["result"]["serverInfo"]["version"] == "test"
