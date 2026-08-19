"""Offline tests for the first-party CUA MCP proxy.

The real runtime has OS permissions and a display dependency, so these tests use a tiny stdio
stand-in. They prove the Qwen proxy resolves open-computer-use without a GUI PATH while owning the
advertised server identity.
"""

from __future__ import annotations

import base64
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


def _png_image(width: int, height: int) -> dict:
    header = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
    header += width.to_bytes(4, "big") + height.to_bytes(4, "big")
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(header).decode()}


def _tool_request(request_id: int, name: str, arguments: dict) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ).encode()
        + b"\n"
    )


def _tool_response(request_id: int, content: list[dict], *, is_error: bool = False) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": content, "isError": is_error},
            }
        ).encode()
        + b"\n"
    )


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


@pytest.mark.parametrize(
    ("configured", "upstream_value"),
    [
        ("1", "1"),
        ("true", "1"),
        ("YES", "1"),
        ("on", "1"),
        ("0", "0"),
        ("false", "0"),
        ("no", "0"),
        ("OFF", "0"),
        ("", "0"),
    ],
)
def test_open_computer_use_environment_maps_global_pointer_switch(monkeypatch, configured, upstream_value):
    monkeypatch.setattr(
        proxy,
        "get_env",
        lambda name: configured if name == "QWEN_MM_CUA_GLOBAL_POINTER_FALLBACKS" else None,
    )

    environment = proxy.open_computer_use_environment({"KEEP": "yes"})

    assert environment == {
        "KEEP": "yes",
        "OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS": upstream_value,
    }


def test_open_computer_use_environment_preserves_direct_upstream_setting_when_unconfigured(monkeypatch):
    monkeypatch.setattr(proxy, "get_env", lambda _: None)
    base = {"OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS": "1"}

    assert proxy.open_computer_use_environment(base) == base


def test_open_computer_use_environment_rejects_invalid_value(monkeypatch):
    monkeypatch.setattr(
        proxy,
        "get_env",
        lambda name: "sometimes" if name == "QWEN_MM_CUA_GLOBAL_POINTER_FALLBACKS" else None,
    )

    with pytest.raises(RuntimeError, match="QWEN_MM_CUA_GLOBAL_POINTER_FALLBACKS"):
        proxy.open_computer_use_environment({})


def test_rewrite_initialize_response_uses_proxy_identity():
    original = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "serverInfo": {"name": "open-computer-use", "version": "0.2.3"},
            "protocolVersion": "2025-06-18",
        },
    }
    rewritten = json.loads(proxy.rewrite_initialize_response(json.dumps(original).encode() + b"\n"))

    assert rewritten["result"]["serverInfo"] == {"name": "qwen-mm-plugins-cua", "version": "1.0.0"}
    assert rewritten["result"]["protocolVersion"] == "2025-06-18"


def test_rewrite_initialize_response_advertises_relative_coordinates():
    original = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "serverInfo": {"name": "open-computer-use", "version": "0.2.3"},
            "instructions": "Inspect before acting.",
        },
    }

    rewritten = json.loads(proxy.rewrite_initialize_response(json.dumps(original).encode() + b"\n"))

    assert "relative values from 0 to 1000" in rewritten["result"]["instructions"]
    assert "(0, 0) is top-left" in rewritten["result"]["instructions"]


def test_rewrite_server_response_advertises_relative_coordinate_schemas():
    state = proxy.ProxyState()
    response = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "click",
                    "description": "pixel click",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                    },
                },
                {
                    "name": "drag",
                    "description": "pixel drag",
                    "inputSchema": {
                        "type": "object",
                        "properties": {name: {"type": "number"} for name in ("from_x", "from_y", "to_x", "to_y")},
                    },
                },
                {
                    "name": "get_app_state",
                    "description": "Return a screenshot.",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
        },
    }

    rewritten = json.loads(proxy.rewrite_server_response(json.dumps(response).encode() + b"\n", state))
    tools = {tool["name"]: tool for tool in rewritten["result"]["tools"]}

    assert tools["click"]["inputSchema"]["properties"]["x"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1000,
        "description": "Relative X coordinate from 0 (left) to 1000 (right) on the latest screenshot",
    }
    assert tools["click"]["inputSchema"]["properties"]["y"]["maximum"] == 1000
    assert tools["click"]["inputSchema"]["anyOf"] == [
        {"required": ["element_index"]},
        {"required": ["x", "y"]},
    ]
    assert tools["drag"]["inputSchema"]["properties"]["to_y"]["minimum"] == 0
    assert "0–1000 relative coordinates" in tools["get_app_state"]["description"]


def test_relative_click_and_drag_are_converted_from_latest_screenshot():
    state = proxy.ProxyState()
    state_request = _tool_request(10, "get_app_state", {"app": "TextEdit"})
    upstream, local = proxy.rewrite_client_request(state_request, state)
    assert upstream == state_request
    assert local is None

    response = _tool_response(10, [{"type": "text", "text": "state"}, _png_image(1200, 800)])
    assert proxy.rewrite_server_response(response, state) == response

    click = _tool_request(11, "click", {"app": "textedit", "x": 500, "y": 250})
    upstream, local = proxy.rewrite_client_request(click, state)
    assert local is None
    click_arguments = json.loads(upstream)["params"]["arguments"]
    assert click_arguments["x"] == 599.5
    assert click_arguments["y"] == 199.75

    drag = _tool_request(
        12,
        "drag",
        {"app": "TextEdit", "from_x": 0, "from_y": 0, "to_x": 1000, "to_y": 1000},
    )
    upstream, local = proxy.rewrite_client_request(drag, state)
    assert local is None
    drag_arguments = json.loads(upstream)["params"]["arguments"]
    assert drag_arguments == {
        "app": "TextEdit",
        "from_x": 0,
        "from_y": 0,
        "to_x": 1199,
        "to_y": 799,
    }


def test_element_click_does_not_need_screenshot_dimensions():
    state = proxy.ProxyState()
    request = _tool_request(20, "click", {"app": "TextEdit", "element_index": "4"})

    upstream, local = proxy.rewrite_client_request(request, state)

    assert upstream == request
    assert local is None


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"app": "TextEdit", "x": 500, "y": 500}, "Call get_app_state"),
        ({"app": "TextEdit", "x": -1, "y": 500}, "x must be between 0 and 1000"),
        ({"app": "TextEdit", "x": 500}, "click relative coordinates require x, y"),
    ],
)
def test_invalid_relative_click_returns_local_tool_error(arguments, message):
    state = proxy.ProxyState()
    if arguments.get("x") == -1:
        state.update_screenshot_size("TextEdit", (1200, 800))
    request = _tool_request(30, "click", arguments)

    upstream, local = proxy.rewrite_client_request(request, state)

    assert upstream is None
    result = json.loads(local)["result"]
    assert result["isError"] is True
    assert message in result["content"][0]["text"]


def test_missing_image_or_turn_end_invalidates_cached_dimensions():
    state = proxy.ProxyState()
    state.update_screenshot_size("TextEdit", (1200, 800))
    request = _tool_request(40, "get_app_state", {"app": "TextEdit"})
    proxy.rewrite_client_request(request, state)
    proxy.rewrite_server_response(_tool_response(40, [{"type": "text", "text": "no image"}]), state)
    assert state.screenshot_size("TextEdit") is None

    state.update_screenshot_size("TextEdit", (1200, 800))
    notification = b'{"jsonrpc":"2.0","method":"notifications/turn-ended"}\n'
    assert proxy.rewrite_client_request(notification, state) == (notification, None)
    assert state.screenshot_size("TextEdit") is None


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
    env = dict(
        os.environ,
        QWEN_MM_OPEN_COMPUTER_USE_PATH=str(fake_runtime),
        QWEN_MM_CUA_GLOBAL_POINTER_FALLBACKS="off",
    )
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
    assert response["result"]["serverInfo"]["version"] == "1.0.0"


def test_proxy_converts_relative_coordinates_end_to_end(tmp_path):
    image_data = _png_image(1200, 800)["data"]
    fake_runtime = _executable(
        tmp_path / "fake-open-computer-use",
        f"""#!{sys.executable}
import json
import os
import sys
for line in sys.stdin:
    request = json.loads(line)
    request_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        fallback = os.environ.get("OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS", "missing")
        result = {{"serverInfo": {{"name": "open-computer-use", "version": "test"}}, "instructions": "pointer fallback=" + fallback}}
    elif method == "tools/list":
        result = {{"tools": [{{"name": "click", "description": "pixel click", "inputSchema": {{"type": "object", "properties": {{"x": {{"type": "number"}}, "y": {{"type": "number"}}}}}}}}]}}
    elif method == "tools/call" and request["params"]["name"] == "get_app_state":
        result = {{"content": [{{"type": "image", "mimeType": "image/png", "data": {image_data!r}}}], "isError": False}}
    elif method == "tools/call" and request["params"]["name"] == "click":
        result = {{"content": [{{"type": "text", "text": json.dumps(request["params"]["arguments"])}}], "isError": False}}
    else:
        continue
    print(json.dumps({{"jsonrpc": "2.0", "id": request_id, "result": result}}), flush=True)
""",
    )
    env = dict(
        os.environ,
        QWEN_MM_OPEN_COMPUTER_USE_PATH=str(fake_runtime),
        QWEN_MM_CUA_GLOBAL_POINTER_FALLBACKS="on",
    )
    process = subprocess.Popen(
        [sys.executable, "src/capabilities/cua/qwen_mm_plugins_cua"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    output: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: [output.put(line) for line in process.stdout], daemon=True)
    reader.start()

    def call(request: dict) -> dict:
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        try:
            return json.loads(output.get(timeout=2))
        except queue.Empty:
            pytest.fail(f"proxy did not respond to {request['method']}")

    try:
        initialize = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert initialize["result"]["serverInfo"]["name"] == "qwen-mm-plugins-cua"
        assert "pointer fallback=1" in initialize["result"]["instructions"]
        assert "relative values from 0 to 1000" in initialize["result"]["instructions"]

        tools = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        x_schema = tools["result"]["tools"][0]["inputSchema"]["properties"]["x"]
        assert (x_schema["minimum"], x_schema["maximum"]) == (0, 1000)

        call(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_app_state", "arguments": {"app": "TextEdit"}},
            }
        )
        clicked = call(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "click", "arguments": {"app": "TextEdit", "x": 500, "y": 250}},
            }
        )
    finally:
        process.stdin.close()
        process.wait(timeout=3)

    forwarded_arguments = json.loads(clicked["result"]["content"][0]["text"])
    assert forwarded_arguments == {"app": "TextEdit", "x": 599.5, "y": 199.75}
    assert process.returncode == 0
