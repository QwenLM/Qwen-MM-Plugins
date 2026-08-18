"""Lightweight end-to-end stdio test for the core server (P3.6b).

Incremental to test_tools.py's protocol tests: those drive the server through the MCP
SDK's *client* (ClientSession). Here the client side is raw newline-delimited JSON-RPC
written straight to the subprocess's stdin — the same way a harness config launches it
(`python3 src/capabilities/core/qwen_mm_plugins_core`) — so a framing/handshake regression
that the SDK client would paper over still gets caught. Asserts the full handshake
(initialize → initialized → tools/list) and that every advertised tool's wire metadata is
complete (name / description / normalized inputSchema).

The *server* needs the `mcp` SDK to run, so this skips when it's absent.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest
from conftest import CORE_SERVER_DIR

pytest.importorskip("mcp")  # the server subprocess can't start without the SDK

pytestmark = pytest.mark.skipif(not CORE_SERVER_DIR, reason="qwen_mm_plugins_core server package not found")


def _rpc(method: str, params: dict | None = None, id_: int | None = None) -> str:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id_ is not None:
        msg["id"] = id_
    return json.dumps(msg) + "\n"


_STDOUT_EOF = object()


def _collect_stdout(stream, messages: queue.Queue) -> None:
    """Decode stdout without blocking the test's request/response handshake."""
    try:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError as exc:
                messages.put(exc)
                return
    finally:
        messages.put(_STDOUT_EOF)


def _send(proc: subprocess.Popen, *messages: str) -> None:
    assert proc.stdin is not None
    proc.stdin.write("".join(messages))
    proc.stdin.flush()


def _wait_for_response(proc: subprocess.Popen, messages: queue.Queue, response_id: int, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for JSON-RPC response {response_id}; process={proc.poll()}")
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty:
            raise AssertionError(
                f"timed out waiting for JSON-RPC response {response_id}; process={proc.poll()}"
            ) from None
        if message is _STDOUT_EOF:
            raise AssertionError(f"server stdout closed before JSON-RPC response {response_id}; process={proc.poll()}")
        if isinstance(message, BaseException):
            raise AssertionError(f"non-JSON output from stdio server: {message}") from message
        if message.get("id") == response_id:
            return message


@pytest.fixture(scope="module")
def rpc_responses() -> dict[int, dict]:
    """Run one full handshake + tools/list against a fresh server subprocess.

    Wait for the initialize response before sending the initialized notification and
    tools/list request, just like a real MCP client. Closing stdin before the server had
    drained every request made this test race against EOF handling on fast Linux runners.
    """
    proc = subprocess.Popen(
        [sys.executable, CORE_SERVER_DIR],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=dict(os.environ),
    )
    assert proc.stdout is not None
    messages: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_collect_stdout, args=(proc.stdout, messages), daemon=True)
    reader.start()

    responses = {}
    try:
        _send(
            proc,
            _rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "e2e-stdio-test", "version": "0"},
                },
                id_=1,
            ),
        )
        responses[1] = _wait_for_response(proc, messages, 1)

        _send(proc, _rpc("notifications/initialized"), _rpc("tools/list", {}, id_=2))
        responses[2] = _wait_for_response(proc, messages, 2)
    finally:
        if proc.stdin is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        reader.join(timeout=1)

    assert proc.stderr is not None
    stderr = proc.stderr.read()
    assert proc.returncode == 0, f"stdio server exited with {proc.returncode}; stderr tail: {stderr[-500:]}"
    return responses


def test_initialize_handshake(rpc_responses):
    init = rpc_responses.get(1)
    assert init and "result" in init, f"initialize failed: {init}"
    result = init["result"]
    assert result.get("protocolVersion")
    assert result.get("serverInfo", {}).get("name")


def test_tools_list_complete_schemas(rpc_responses):
    listed = rpc_responses.get(2)
    assert listed and "result" in listed, f"tools/list failed: {listed}"
    tools = listed["result"]["tools"]
    assert len(tools) > 0

    names = set()
    for tool in tools:
        assert tool.get("name"), f"tool without a name: {tool}"
        names.add(tool["name"])
        assert tool.get("description", "").strip(), f"{tool['name']}: empty description"
        schema = tool.get("inputSchema")
        assert isinstance(schema, dict) and schema.get("type") == "object", f"{tool['name']}: bad inputSchema"
        assert isinstance(schema.get("properties"), dict), f"{tool['name']}: inputSchema must list properties"
        # tool_schema() normalization: no auto-titles, no unresolved $refs on the wire
        blob = json.dumps(schema)
        assert "$ref" not in blob, f"{tool['name']}: inputSchema leaked an unresolved $ref"
    assert len(names) == len(tools), "tool names must be unique"
    # spot-check one stable core tool made it through with its real argument
    read_image = next(t for t in tools if t["name"] == "read_image")
    assert "image_path" in read_image["inputSchema"]["properties"]
