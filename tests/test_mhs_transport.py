"""Exercise the actual HTTP boundary, including ambiguous outcomes of a hardware write."""

import contextlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import msgpack
import pytest

from qwen_mm_plugins_mhs import http_client
from qwen_mm_plugins_mhs.config import Adapter


@contextlib.contextmanager
def endpoint(*, body=b"\x80", status=200, content_type="application/msgpack", outcome="reply"):
    calls = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append((self.path, self.headers, self.rfile.read(int(self.headers["Content-Length"]))))
            if outcome == "timeout":
                release.wait(3)
                return
            if outcome == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if 300 <= status < 400:
                self.send_header("Location", "/mhs/v1/devices/d/write/again")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            Adapter("test", f"http://127.0.0.1:{server.server_port}", timeout=0.2 if outcome == "timeout" else 3),
            calls,
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_binary_maps_preserve_dynamic_values_and_multiple_images():
    params = {"enabled": False, "zero": 0, "timestamp": 2**63 + 1, "name": "前相机", "optional": None}
    response = {
        "data": params,
        "blocks": [
            {"type": "image", "data": b"\xff\xd8\x00\xfe", "mimeType": "image/jpeg"},
            {"type": "text", "text": "two cameras"},
            {"type": "image", "data": b"\x89PNG\x00\xff", "mimeType": "image/png"},
        ],
    }
    with endpoint(body=msgpack.packb(response, use_bin_type=True)) as (adapter, calls):
        actual = http_client.request(adapter, "POST", "/devices/d/read/cameras", params)
    assert actual == response
    assert len(calls) == 1
    path, headers, raw = calls[0]
    assert path.endswith("/read/cameras")
    assert headers["Content-Type"] == headers["Accept"] == "application/msgpack"
    assert msgpack.unpackb(raw, raw=False) == params


@pytest.mark.parametrize(
    "options,match",
    [
        ({"body": b"{}", "content_type": "application/json"}, "Content-Type"),
        ({"body": b"{}"}, "valid MessagePack"),
        ({"body": b""}, "valid MessagePack"),
        ({"body": b"\x81"}, "valid MessagePack"),
        ({"body": b"\x80\x80"}, "valid MessagePack"),
        ({"body": b"\xc0"}, "expected a map"),
        ({"body": b"\x81\xa1x\xdd\xff\xff\xff\xff"}, "valid MessagePack"),
        ({"outcome": "disconnect"}, "failed"),
        ({"outcome": "timeout"}, "timed out"),
        ({"status": 307}, "HTTP 307"),
    ],
)
def test_failed_or_ambiguous_write_is_never_reissued(options, match):
    with endpoint(**options) as (adapter, calls):
        with pytest.raises(http_client.AdapterError, match=match):
            http_client.request(adapter, "POST", "/devices/d/write/move", {"vx": 0.1})
    assert len(calls) == 1


def test_structured_binary_error_keeps_status_code_and_message():
    body = msgpack.packb({"error": {"code": "blocked", "message": "motion refused"}}, use_bin_type=True)
    with endpoint(body=body, status=409) as (adapter, calls):
        with pytest.raises(http_client.AdapterError, match="motion refused") as exc:
            http_client.request(adapter, "POST", "/devices/d/write/move", {})
    assert exc.value.status == 409 and exc.value.code == "blocked"
    assert len(calls) == 1


def test_response_size_is_capped_before_decoding(monkeypatch):
    monkeypatch.setattr(http_client, "MAX_BODY_BYTES", 32)
    with endpoint(body=msgpack.packb({"data": b"x" * 100})) as (adapter, calls):
        with pytest.raises(http_client.AdapterError, match="more than 32 bytes"):
            http_client.request(adapter, "POST", "/devices/d/read/frame", {})
    assert len(calls) == 1


def test_unencodable_request_does_not_reach_adapter():
    with endpoint() as (adapter, calls):
        with pytest.raises(http_client.AdapterError, match="cannot encode"):
            http_client.request(adapter, "POST", "/devices/d/write/settings", {"value": 2**65})
    assert calls == []
