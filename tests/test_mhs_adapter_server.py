"""The copyable adapter must enforce its own contract, even without the MHS host."""

import threading
from http.client import HTTPConnection
from urllib.parse import quote

import msgpack
import pytest
from test_mhs import _load_mock_adapter


@pytest.fixture(scope="module")
def example():
    module = _load_mock_adapter()
    server = module.build_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield module, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, path, payload=None, *, raw=None, headers=None, method="POST"):
    connection = HTTPConnection(*server.server_address, timeout=3)
    body = msgpack.packb(payload or {}, use_bin_type=True) if raw is None else raw
    try:
        connection.request(
            method,
            "/mhs/v1" + path,
            body=body if method == "POST" else None,
            headers={"Content-Type": "application/msgpack", **(headers or {})},
        )
        response = connection.getresponse()
        return response.status, msgpack.unpackb(response.read(), raw=False)
    finally:
        connection.close()


@pytest.mark.parametrize("exposure", [0, 101, True, 1.5, "40", None])
def test_camera_enforces_hard_limits_and_types_directly(example, exposure):
    _, server = example
    camera = server.devices["mock-camera"]
    before = dict(camera.settings)
    status, body = request(server, "/devices/mock-camera/write/settings", {"exposure": exposure})
    assert status == 400 and body["error"]["code"] == "bad_parameter"
    assert camera.settings == before


def test_camera_update_is_atomic_and_soft_limit_stays_advisory(example):
    _, server = example
    camera = server.devices["mock-camera"]
    before = dict(camera.settings)
    status, _ = request(server, "/devices/mock-camera/write/settings", {"exposure": 70, "height": 900})
    assert status == 400
    assert camera.settings == before
    status, body = request(server, "/devices/mock-camera/write/settings", {"gain": 33})
    assert status == 200 and body["ok"]
    assert camera.settings["gain"] == 33


def test_camera_read_override_does_not_change_settings(example):
    _, server = example
    camera = server.devices["mock-camera"]
    before = dict(camera.settings)
    status, body = request(server, "/devices/mock-camera/read/frame", {"width": 16, "height": 8})
    assert status == 200 and "16x8" in body["blocks"][1]["text"]
    assert camera.settings == before


@pytest.mark.parametrize(
    "path,params",
    [
        ("mock-camera/read/frame", {"width": True}),
        ("mock-camera/read/frame", {"gain": 8}),
        ("mock-camera/write/settings", {"exposure": 50, "unknown": 1}),
        ("mock-camera/read/settings", {"exposure": 50}),
        ("mock-lamp/write/power", {"on": "false"}),
        ("mock-lamp/write/power", {}),
        ("mock-lamp/write/brightness", {"level": -1}),
        ("mock-camera/reset", {"mode": "restart"}),
        ("mock-camera/reset", {"mode": "soft", "extra": True}),
    ],
)
def test_invalid_parameters_are_errors_not_silent_conversions(example, path, params):
    _, server = example
    status, body = request(server, "/devices/" + path, params)
    assert status == 400 and body["error"]["code"] == "bad_parameter"


@pytest.mark.parametrize(
    "raw",
    [
        b"\x91\x01",
        b"\xc0",
        b"\xc1",
        b"\x81",
        b"\x80\x80",
        b"{}",
        msgpack.packb({"exposure": float("nan")}),
        msgpack.packb({"exposure": float("inf")}),
        msgpack.packb({"exposure": {"nested": float("inf")}}),
        msgpack.packb({1: "value"}),
        msgpack.packb({b"exposure": 40}),
        msgpack.packb({"exposure": msgpack.ExtType(1, b"")}),
        b"\x81\xa8exposure\xdd\xff\xff\xff\xff",
    ],
)
def test_malformed_messagepack_does_not_reach_device(example, raw):
    _, server = example
    camera = server.devices["mock-camera"]
    before = dict(camera.settings)
    status, body = request(server, "/devices/mock-camera/write/settings", raw=raw)
    assert status == 400 and body["error"]["code"] == "bad_messagepack"
    assert camera.settings == before


@pytest.mark.parametrize("length,status", [("no", 400), ("-1", 400), ("65537", 413)])
def test_invalid_or_oversized_body_length_is_rejected_without_reading(example, length, status):
    _, server = example
    actual, body = request(server, "/devices/mock-camera/write/settings", raw=b"", headers={"Content-Length": length})
    assert actual == status and "error" in body


def test_each_server_owns_fresh_devices(example):
    module, first = example
    second = module.build_server()
    try:
        assert first.devices["mock-camera"] is not second.devices["mock-camera"]
        second.devices["mock-camera"].write("settings", {"exposure": 95})
        assert first.devices["mock-camera"].settings["exposure"] != 95
    finally:
        second.server_close()


@pytest.mark.parametrize("content_type", ["application/json", "application/octet-stream", "text/plain"])
def test_only_messagepack_is_accepted_and_rejection_does_not_mutate_device(example, content_type):
    _, server = example
    camera = server.devices["mock-camera"]
    before = dict(camera.settings)
    status, body = request(
        server, "/devices/mock-camera/write/settings", {"exposure": 75}, headers={"Content-Type": content_type}
    )
    assert status == 415 and body["error"]["code"] == "unsupported_media_type"
    assert camera.settings == before


def test_deeply_nested_parameters_are_rejected_before_device_io(example):
    _, server = example
    raw = b"\x81\xa8exposure" + b"\x91" * 70 + b"\xc0"
    status, body = request(server, "/devices/mock-camera/write/settings", raw=raw)
    assert status == 400 and "nesting" in body["error"]["message"]


@pytest.mark.parametrize("response", [None, [], {"value": float("inf")}])
def test_invalid_device_response_becomes_protocol_error(example, monkeypatch, response):
    _, server = example
    monkeypatch.setattr(server.devices["mock-camera"], "health", lambda: response)
    status, body = request(server, "/devices/mock-camera/health", method="GET")
    assert status == 500 and body["error"]["code"] == "internal"


def test_duplicate_ids_are_rejected_before_binding(example):
    module, _ = example
    with pytest.raises(ValueError, match="Duplicate device_id"):
        module.AdapterServer(("127.0.0.1", 0), [module.MockCamera(), module.MockCamera()])


def test_url_encoded_device_ids_and_optional_reset(example):
    module, _ = example
    lamp = module.MockLamp()
    lamp.device_id = "lamp / one"
    server = module.AdapterServer(("127.0.0.1", 0), [lamp])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = "/devices/" + quote(lamp.device_id, safe="")
        status, body = request(server, path + "/read/power")
        assert status == 200 and body["blocks"][0]["value"] is False
        status, body = request(server, path + "/reset")
        assert status == 405 and body["error"]["code"] == "reset_unsupported"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
