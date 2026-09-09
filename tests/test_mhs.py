"""Tests for the mhs MCP server (a Model Hardware Standard host).

The end-to-end cases run the bundled mock adapter over real HTTP on an ephemeral port, so the
protocol is exercised for real — request routing, JSON shapes, base64 image transport, HTTP error
codes — while staying offline and credential-free.

conftest auto-discovers qwen_mm_plugins_mhs; the mock adapter ships beside the Skill (not as an
importable package, since it is a template to copy), so it is loaded by path.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler

import pytest
from conftest import REPO_ROOT

import qwen_mm_plugins_mhs as mhs
from qwen_mm_plugins_mhs import config, protocol, registry

_CAP_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "mhs")
_MOCK_ADAPTER = os.path.join(_CAP_DIR, "skill", "references", "mock_adapter.py")

EXPECTED_TOOLS = {
    "mhs_discover",
    "mhs_meta_info",
    "mhs_read",
    "mhs_write",
    "mhs_health_check",
    "mhs_reset",
}


def _load_mock_adapter():
    spec = importlib.util.spec_from_file_location("mhs_mock_adapter", _MOCK_ADAPTER)
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(os.path.dirname(_MOCK_ADAPTER))
        spec.loader.exec_module(module)
    return module


def _blocks_text(blocks) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b["type"] == "text")


def _call(name: str, **kwargs):
    return mhs.get_handler(name)(kwargs)


def _write_registry(tmp_path, adapters) -> str:
    path = tmp_path / "mhs-devices.json"
    path.write_text(json.dumps({"adapters": adapters}))
    return str(path)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ── fixtures ──
@pytest.fixture(autouse=True)
def _clear_caches():
    """Device lists and metadata are cached in-process; isolate every test."""
    registry.invalidate()
    yield
    registry.invalidate()


@pytest.fixture(scope="module")
def mock_adapter():
    """The bundled mock adapter, serving on an ephemeral port for the whole module."""
    module = _load_mock_adapter()
    server = module.build_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield {"module": module, "server": server, "url": f"http://{host}:{port}"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def live(mock_adapter, tmp_path, monkeypatch):
    """A registry pointing one adapter named 'mock' at the running mock adapter."""
    path = _write_registry(tmp_path, [{"name": "mock", "url": mock_adapter["url"]}])
    monkeypatch.setenv("QWEN_MM_MHS_DEVICES", path)
    # Reset simulated device state so tests don't inherit each other's writes.
    _call("mhs_reset", device_id="mock/mock-camera")
    registry.invalidate()
    return mock_adapter


# ── the fixed tool surface ──
def test_exposes_exactly_the_six_fixed_tools():
    """The MHS surface is fixed: new hardware must not add tools."""
    assert {t["name"] for t in mhs.list_tools()} == EXPECTED_TOOLS


def test_every_tool_has_a_handler_and_a_description():
    for tool in mhs.list_tools():
        assert mhs.get_handler(tool["name"]) is not None
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


def test_write_tool_advertises_that_it_touches_real_hardware():
    """The description is the only warning the model gets before it moves something."""
    description = next(t["description"] for t in mhs.list_tools() if t["name"] == "mhs_write")
    assert "REAL HARDWARE" in description


# ── configuration ──
def test_missing_registry_file_explains_how_to_create_it(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_MHS_DEVICES", str(tmp_path / "absent.json"))
    blocks = _call("mhs_discover")
    text = _blocks_text(blocks)
    assert "Error:" in text
    assert "absent.json" in text
    assert '"adapters"' in text  # shows the shape to create


def test_malformed_registry_file_reports_the_path_and_the_parse_error(tmp_path, monkeypatch):
    path = tmp_path / "mhs-devices.json"
    path.write_text("{not json")
    monkeypatch.setenv("QWEN_MM_MHS_DEVICES", str(path))
    text = _blocks_text(_call("mhs_discover"))
    assert "Error:" in text and "not valid JSON" in text


def test_devices_file_defaults_beside_the_shared_config(monkeypatch):
    monkeypatch.delenv("QWEN_MM_MHS_DEVICES", raising=False)
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", "/tmp/qmp-test-config")
    assert config.devices_file() == "/tmp/qmp-test-config/mhs-devices.json"


@pytest.mark.parametrize(
    ("adapters", "expected"),
    [
        ([{"name": "a", "url": "ftp://host"}], "http:// or https://"),
        ([{"name": "a", "url": "http://h"}, {"name": "a", "url": "http://h2"}], "duplicate adapter name"),
        ([{"name": "a/b", "url": "http://h"}], "must not contain"),
        ([{"name": "a", "url": "http://h", "timeout": 0}], "timeout must be a number"),
        ([{"name": "a", "url": "http://h", "auth": {"type": "basic"}}], 'auth.type must be "bearer"'),
        ([{"name": "a", "url": "http://h", "auth": {"type": "bearer"}}], "token_env"),
        ([], "lists no adapters"),
    ],
)
def test_registry_validation_rejects_bad_entries(tmp_path, monkeypatch, adapters, expected):
    monkeypatch.setenv("QWEN_MM_MHS_DEVICES", _write_registry(tmp_path, adapters))
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_adapters()
    assert expected in str(excinfo.value)


def test_bearer_token_is_referenced_by_env_var_not_stored(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QWEN_MM_MHS_DEVICES",
        _write_registry(
            tmp_path, [{"name": "a", "url": "http://h", "auth": {"type": "bearer", "token_env": "MY_TOKEN"}}]
        ),
    )
    adapter = config.load_adapters()[0]
    assert adapter.token_env == "MY_TOKEN"
    monkeypatch.delenv("MY_TOKEN", raising=False)
    assert adapter.token() is None
    monkeypatch.setenv("MY_TOKEN", "abc123")
    assert adapter.token() == "abc123"  # resolved at call time, so rotation needs no restart


# ── the safety gate, checked before anything reaches the network ──
def _meta(**overrides):
    meta = {
        "device_id": "mock/dev",
        "capabilities": [
            {"name": "move", "direction": "both", "requires_confirm": False},
            {"name": "temperature", "direction": "read", "requires_confirm": False},
            {"name": "fire", "direction": "write", "requires_confirm": True},
        ],
        "safety_limits": [
            {"parameter": "speed", "min": 0.0, "max": 50.0, "unit": "mm/s", "hard": True, "description": ""},
            {"parameter": "gain", "min": 0.0, "max": 10.0, "unit": "dB", "hard": False, "description": ""},
        ],
    }
    meta.update(overrides)
    return meta


def test_hard_limit_violation_is_refused_and_cannot_be_confirmed_away():
    for confirm in (False, True):
        problem = protocol.check_write(_meta(), "move", {"speed": 500}, confirm)
        assert problem is not None
        assert "hard safety limit" in problem and "cannot be overridden" in problem


def test_soft_limit_violation_is_refused_without_confirm_and_allowed_with_it():
    assert "soft limit" in protocol.check_write(_meta(), "move", {"gain": 99}, False)
    assert protocol.check_write(_meta(), "move", {"gain": 99}, True) is None


def test_within_limits_passes():
    assert protocol.check_write(_meta(), "move", {"speed": 10, "gain": 2}, False) is None


def test_requires_confirm_capability_needs_the_flag():
    assert "requiring confirmation" in protocol.check_write(_meta(), "fire", {}, False)
    assert protocol.check_write(_meta(), "fire", {}, True) is None


def test_direction_is_enforced_both_ways():
    assert "read-only" in protocol.check_write(_meta(), "temperature", {}, False)
    assert "write-only" in protocol.check_read(_meta(), "fire")


def test_unknown_capability_names_the_ones_that_exist():
    problem = protocol.check_read(_meta(), "nope")
    assert "has no capability 'nope'" in problem
    assert "move" in problem and "temperature" in problem


def test_limit_with_only_one_bound_is_honoured():
    meta = _meta(safety_limits=[{"parameter": "p", "min": 5.0, "max": None, "unit": "", "hard": True}])
    assert "below the declared minimum" in protocol.check_write(meta, "move", {"p": 1}, False)
    assert protocol.check_write(meta, "move", {"p": 1000}, False) is None


def test_limit_defaults_to_hard_when_the_adapter_omits_it():
    """A limit whose strictness was forgotten must not be treated as advisory."""
    normalized = protocol.normalize_meta({"safety_limits": [{"parameter": "p", "max": 1}]}, "a/b")
    assert normalized["safety_limits"][0]["hard"] is True


def test_out_of_range_write_never_reaches_the_network(tmp_path, monkeypatch):
    """Pointing at a dead port proves the refusal happened locally: a connection error would differ."""
    from qwen_mm_plugins_mhs.tools import mhs_write

    dead = f"http://127.0.0.1:{_free_port()}"
    monkeypatch.setenv("QWEN_MM_MHS_DEVICES", _write_registry(tmp_path, [{"name": "x", "url": dead}]))
    # Patch the name the tool module bound, not registry's, and skip the metadata fetch so the only
    # thing that could touch the network is the write itself.
    monkeypatch.setattr(
        mhs_write, "meta_of", lambda device_id, refresh=False: (config.load_adapters()[0], "dev", _meta())
    )
    text = _blocks_text(_call("mhs_write", device_id="x/dev", capability="move", params={"speed": 500}))
    assert "hard safety limit" in text
    assert "unreachable" not in text


# ── end-to-end against the mock adapter, over real HTTP ──
def test_discover_lists_both_mock_devices_with_qualified_ids(live):
    report = json.loads(_call("mhs_discover")[0]["text"])
    ids = {d["device_id"] for d in report["devices"]}
    assert ids == {"mock/mock-camera", "mock/mock-lamp"}
    camera = next(d for d in report["devices"] if d["device_id"] == "mock/mock-camera")
    assert set(camera["capabilities"]) == {"frame", "settings", "temperature"}
    assert "unreachable_adapters" not in report


def test_discover_filters_by_type_and_tag(live):
    by_type = json.loads(_call("mhs_discover", device_type="camera")[0]["text"])
    assert [d["device_id"] for d in by_type["devices"]] == ["mock/mock-camera"]
    by_tag = json.loads(_call("mhs_discover", tag="vision")[0]["text"])
    assert [d["device_id"] for d in by_tag["devices"]] == ["mock/mock-camera"]
    empty = json.loads(_call("mhs_discover", device_type="submarine")[0]["text"])
    assert empty["devices"] == []


def test_meta_info_returns_capabilities_and_surfaces_hard_limits(live):
    blocks = _call("mhs_meta_info", device_id="mock/mock-camera")
    meta = json.loads(blocks[0]["text"])
    assert meta["model"] == "MockCam-1"
    assert {c["name"] for c in meta["capabilities"]} == {"frame", "settings", "temperature"}
    # The hard limit is called out in prose, not only buried in the JSON.
    assert "exposure: [1, 100] ms" in _blocks_text(blocks)


def test_bare_device_id_resolves_when_unambiguous(live):
    meta = json.loads(_call("mhs_meta_info", device_id="mock-camera")[0]["text"])
    assert meta["device_id"] == "mock/mock-camera"


def test_read_frame_returns_a_real_image(live):
    blocks = _call("mhs_read", device_id="mock/mock-camera", capability="frame", params={"width": 32, "height": 24})
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 1
    assert images[0]["mimeType"] == "image/png"
    assert base64.b64decode(images[0]["data"]).startswith(b"\x89PNG\r\n\x1a\n")


def test_read_value_capability_renders_name_value_and_unit(live):
    text = _blocks_text(_call("mhs_read", device_id="mock/mock-camera", capability="temperature"))
    assert "temperature = " in text and "C" in text


def test_write_then_read_shows_the_device_actually_changed(live):
    accepted = _blocks_text(
        _call("mhs_write", device_id="mock/mock-camera", capability="settings", params={"exposure": 75})
    )
    assert "accepted" in accepted
    settings = _blocks_text(_call("mhs_read", device_id="mock/mock-camera", capability="settings"))
    assert "exposure = 75" in settings


def test_camera_hard_limit_is_enforced_end_to_end(live):
    text = _blocks_text(
        _call("mhs_write", device_id="mock/mock-camera", capability="settings", params={"exposure": 5000})
    )
    assert "hard safety limit" in text
    # And the device was left alone.
    assert "exposure = 5000" not in _blocks_text(_call("mhs_read", device_id="mock/mock-camera", capability="settings"))


def test_lamp_power_write_requires_confirmation_end_to_end(live):
    refused = _blocks_text(_call("mhs_write", device_id="mock/mock-lamp", capability="power", params={"on": True}))
    assert "requiring confirmation" in refused
    accepted = _blocks_text(
        _call("mhs_write", device_id="mock/mock-lamp", capability="power", params={"on": True}, confirm=True)
    )
    assert "accepted" in accepted


def test_health_check_one_device_and_all_devices(live):
    one = json.loads(_call("mhs_health_check", device_id="mock/mock-camera")[0]["text"])
    assert one["healthy"] is True and one["state"] == "online"
    every = json.loads(_call("mhs_health_check")[0]["text"])
    assert {d["device_id"] for d in every["devices"]} == {"mock/mock-camera", "mock/mock-lamp"}


def test_reset_soft_and_estop_both_succeed(live):
    assert "soft reset completed" in _blocks_text(_call("mhs_reset", device_id="mock/mock-camera"))
    assert "emergency stop completed" in _blocks_text(_call("mhs_reset", device_id="mock/mock-camera", mode="estop"))


def test_reset_on_a_device_that_does_not_support_it_says_so_plainly(live):
    """Reset is optional in MHS; the model must learn this device cannot be stopped through it."""
    text = _blocks_text(_call("mhs_reset", device_id="mock/mock-lamp"))
    assert "does not implement reset" in text
    assert "cannot be stopped" in text


def test_estop_needs_no_confirmation_flag(live):
    """Nothing may stand between the model and a stop."""
    schema = next(t["inputSchema"] for t in mhs.list_tools() if t["name"] == "mhs_reset")
    assert "confirm" not in schema["properties"]
    assert set(schema["properties"]) == {"device_id", "mode"}


# ── failure paths: an adapter that is wrong, absent, or hostile ──
def test_unknown_device_is_an_error_block_not_an_exception(live):
    text = _blocks_text(_call("mhs_read", device_id="mock/no-such-device", capability="frame"))
    assert "Error:" in text and "unknown_device" in text


def test_unknown_adapter_lists_the_configured_ones(live):
    text = _blocks_text(_call("mhs_read", device_id="ghost/dev", capability="frame"))
    assert "no adapter named 'ghost'" in text and "mock" in text


def test_unreachable_adapter_is_reported_per_adapter_without_hiding_working_ones(mock_adapter, tmp_path, monkeypatch):
    dead = f"http://127.0.0.1:{_free_port()}"
    monkeypatch.setenv(
        "QWEN_MM_MHS_DEVICES",
        _write_registry(
            tmp_path,
            [
                {"name": "mock", "url": mock_adapter["url"]},
                {"name": "dead", "url": dead, "timeout": 2},
            ],
        ),
    )
    report = json.loads(_call("mhs_discover")[0]["text"])
    assert {d["device_id"] for d in report["devices"]} == {"mock/mock-camera", "mock/mock-lamp"}
    assert "dead" in report["unreachable_adapters"]


def test_wrong_bearer_token_is_reported_per_adapter(tmp_path, monkeypatch):
    module = _load_mock_adapter()
    server = module.build_server(port=0, token="right-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        monkeypatch.setenv(
            "QWEN_MM_MHS_DEVICES",
            _write_registry(
                tmp_path,
                [
                    {
                        "name": "secure",
                        "url": f"http://{host}:{port}",
                        "auth": {"type": "bearer", "token_env": "MHS_TEST_TOKEN"},
                    }
                ],
            ),
        )
        monkeypatch.setenv("MHS_TEST_TOKEN", "wrong-token")
        registry.invalidate()
        # discover degrades per adapter rather than failing outright, so the 401 lands there.
        report = json.loads(_call("mhs_discover")[0]["text"])
        assert report["devices"] == []
        assert "401" in report["unreachable_adapters"]["secure"]

        monkeypatch.setenv("MHS_TEST_TOKEN", "right-token")
        registry.invalidate()
        report = json.loads(_call("mhs_discover")[0]["text"])
        assert len(report["devices"]) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_configured_auth_with_an_unset_token_env_is_reported_not_sent(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QWEN_MM_MHS_DEVICES",
        _write_registry(
            tmp_path,
            [
                {
                    "name": "secure",
                    "url": "http://127.0.0.1:1",
                    "auth": {"type": "bearer", "token_env": "ABSENT_TOKEN"},
                }
            ],
        ),
    )
    monkeypatch.delenv("ABSENT_TOKEN", raising=False)
    text = _blocks_text(_call("mhs_discover"))
    assert "ABSENT_TOKEN is not set" in text


# ── untrusted adapter output ──
def test_invalid_base64_image_degrades_to_text_instead_of_reaching_the_harness():
    blocks = protocol.to_content_blocks([{"type": "image", "data": "not!base64", "mimeType": "image/png"}], "dev")
    assert blocks[0]["type"] == "text" and "not valid base64" in blocks[0]["text"]


def test_oversized_image_is_refused_as_text():
    huge = base64.b64encode(b"\x00" * (protocol.MAX_BODY_BYTES + 10)).decode("ascii")
    blocks = protocol.to_content_blocks([{"type": "image", "data": huge}], "dev")
    assert blocks[0]["type"] == "text" and "over the" in blocks[0]["text"]


def test_image_block_with_neither_data_nor_path_degrades_to_text():
    blocks = protocol.to_content_blocks([{"type": "image"}], "dev")
    assert blocks[0]["type"] == "text" and "neither 'data' nor 'path'" in blocks[0]["text"]


def test_image_by_path_is_read_from_disk(tmp_path):
    png = tmp_path / "f.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    blocks = protocol.to_content_blocks([{"type": "image", "path": str(png), "mimeType": "image/png"}], "dev")
    assert blocks[0]["type"] == "image"
    assert base64.b64decode(blocks[0]["data"]).startswith(b"\x89PNG")


def test_unreadable_image_path_degrades_to_text():
    blocks = protocol.to_content_blocks([{"type": "image", "path": "/nonexistent/x.png"}], "dev")
    assert blocks[0]["type"] == "text" and "cannot see" in blocks[0]["text"]


def test_unrecognized_block_type_is_preserved_as_text_not_dropped():
    """A future block type must degrade, never silently lose a reading."""
    blocks = protocol.to_content_blocks([{"type": "waveform", "samples": [1, 2, 3]}], "dev")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text" and "waveform" in blocks[0]["text"]


def test_bare_string_capability_shorthand_is_accepted():
    meta = protocol.normalize_meta({"capabilities": ["frame", {"name": "settings", "direction": "write"}]}, "a/b")
    assert protocol.capability_names(meta) == ["frame", "settings"]
    assert protocol.check_read(meta, "frame") is None  # shorthand means read+write


def test_blocks_must_be_a_list():
    with pytest.raises(protocol.ProtocolError):
        protocol.to_content_blocks({"type": "text"}, "dev")


# ── the adapter conformance checker ──
class _CannedHandler(BaseHTTPRequestHandler):
    """Serves a fixed {(method, path): (status, payload)} map, for adapters that are wrong on purpose."""

    routes: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._reply("GET")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._reply("POST")

    def _reply(self, method):
        status, payload = self.routes.get((method, self.path.split("?")[0]), (404, {"error": {"code": "nf"}}))
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def _canned_adapter(routes):
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (_CannedHandler,), {"routes": routes}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _one_device(meta, read=None):
    """Routes for a single device 'd' with the given metadata and optional read payload."""
    routes = {
        ("GET", "/mhs/v1/devices"): (200, {"devices": [{"device_id": "d", "capabilities": ["c"]}]}),
        ("GET", "/mhs/v1/devices/d"): (200, meta),
        ("GET", "/mhs/v1/devices/d/health"): (200, {"state": "online", "healthy": True}),
    }
    if read is not None:
        routes[("POST", "/mhs/v1/devices/d/read/c")] = (200, read)
    return routes


def test_verifier_passes_the_shipped_mock_adapter(mock_adapter):
    """The bundled template must be exemplary — it is what people copy."""
    from qwen_mm_plugins_mhs import verify as verifier

    report = verifier.verify(mock_adapter["url"])
    assert report.failed == 0, report.render()
    assert report.warned == 0, report.render()
    assert report.passed > 0


def test_verifier_never_writes_or_resets(mock_adapter):
    """Read-only is the whole safety premise: a checker must not actuate hardware to check it."""
    from qwen_mm_plugins_mhs import verify as verifier

    devices = mock_adapter["server"].devices
    lamp = devices["mock-lamp"]
    camera = devices["mock-camera"]
    before = (lamp.on, lamp.brightness, dict(camera.settings))

    report = verifier.verify(mock_adapter["url"])

    assert (lamp.on, lamp.brightness, dict(camera.settings)) == before
    rendered = report.render()
    assert "NOT called" in rendered and "NOT sent" in rendered


def test_verifier_reports_an_unreachable_adapter():
    from qwen_mm_plugins_mhs import verify as verifier

    report = verifier.verify(f"http://127.0.0.1:{_free_port()}", timeout=2)
    assert report.failed >= 1
    assert "unreachable" in report.render()


def test_verifier_rejects_a_missing_devices_list():
    from qwen_mm_plugins_mhs import verify as verifier

    with _canned_adapter({("GET", "/mhs/v1/devices"): (200, {"stuff": []})}) as url:
        report = verifier.verify(url)
    assert report.failed >= 1
    assert "no 'devices' list" in report.render()


def test_verifier_flags_a_read_response_without_blocks():
    from qwen_mm_plugins_mhs import verify as verifier

    meta = {"description": "x", "capabilities": [{"name": "c", "direction": "read"}]}
    with _canned_adapter(_one_device(meta, read={"value": 1})) as url:
        report = verifier.verify(url)
    assert report.failed >= 1
    assert "no 'blocks' key" in report.render()


def test_verifier_flags_a_malformed_image_block():
    from qwen_mm_plugins_mhs import verify as verifier

    meta = {"description": "x", "capabilities": [{"name": "c", "direction": "read"}]}
    read = {"blocks": [{"type": "image", "data": "not!base64"}]}
    with _canned_adapter(_one_device(meta, read=read)) as url:
        report = verifier.verify(url)
    assert report.failed >= 1
    assert "could not be used" in report.render()


def test_verifier_warns_when_direction_was_omitted_but_not_when_both_is_explicit():
    """An honest bidirectional capability must not be nagged; a forgotten direction must be."""
    from qwen_mm_plugins_mhs import verify as verifier

    read = {"blocks": [{"type": "value", "name": "v", "value": 1}]}

    omitted = {"description": "x", "capabilities": [{"name": "c"}]}
    with _canned_adapter(_one_device(omitted, read=read)) as url:
        report = verifier.verify(url)
    assert "no valid 'direction'" in report.render()

    explicit = {"description": "x", "capabilities": [{"name": "c", "direction": "both"}]}
    with _canned_adapter(_one_device(explicit, read=read)) as url:
        report = verifier.verify(url)
    assert "no valid 'direction'" not in report.render()


def test_verifier_warns_about_a_writable_capability_with_no_limits():
    from qwen_mm_plugins_mhs import verify as verifier

    meta = {"description": "x", "capabilities": [{"name": "c", "direction": "write"}], "safety_limits": []}
    with _canned_adapter(_one_device(meta)) as url:
        report = verifier.verify(url)
    assert report.warned >= 1
    assert "no safety_limits" in report.render()


def test_verifier_rejects_a_safety_limit_with_no_bound():
    from qwen_mm_plugins_mhs import verify as verifier

    meta = {
        "description": "x",
        "capabilities": [{"name": "c", "direction": "write"}],
        "safety_limits": [{"parameter": "p", "unit": "mm"}],
    }
    with _canned_adapter(_one_device(meta)) as url:
        report = verifier.verify(url)
    assert report.failed >= 1
    assert "neither 'min' nor 'max'" in report.render()


def test_verifier_rejects_an_adapter_that_answers_for_a_nonexistent_device():
    from qwen_mm_plugins_mhs import verify as verifier

    meta = {"description": "x", "capabilities": [{"name": "c", "direction": "read"}]}
    routes = _one_device(meta, read={"blocks": []})
    # Answer 200 for anything, including the impossible device id the checker probes with.
    routes[("GET", f"/mhs/v1/devices/{verifier._ABSENT_DEVICE}")] = (200, meta)
    with _canned_adapter(routes) as url:
        report = verifier.verify(url)
    assert report.failed >= 1
    assert "nonexistent device succeeded" in report.render()


def test_verifier_warns_on_a_missing_description():
    from qwen_mm_plugins_mhs import verify as verifier

    meta = {"capabilities": [{"name": "c", "direction": "read"}]}
    with _canned_adapter(_one_device(meta, read={"blocks": [{"type": "text", "text": "hi"}]})) as url:
        report = verifier.verify(url)
    assert "no 'description'" in report.render()


def test_verifier_exit_code_tracks_failures(mock_adapter):
    from qwen_mm_plugins_mhs import verify as verifier

    assert verifier.main([mock_adapter["url"]]) == 0
    assert verifier.main([f"http://127.0.0.1:{_free_port()}", "--timeout", "2"]) == 1


# ── packaging contract ──
def test_capability_declares_no_system_dependencies():
    """A stdlib HTTP client needs none; --check-system must not imply otherwise."""
    assert mhs.SYSTEM_DEPS == []


def test_usage_note_points_at_the_registry_file_and_the_protocol():
    assert "mhs-devices.json" in mhs.USAGE_NOTE
    assert "adapter_protocol.md" in mhs.USAGE_NOTE


def test_skill_and_references_ship_with_the_capability():
    for rel in ("skill/SKILL.md", "skill/references/adapter_protocol.md", "skill/references/mock_adapter.py"):
        assert os.path.isfile(os.path.join(_CAP_DIR, rel)), rel


def test_server_starts_over_stdio_and_lists_its_tools():
    """A real initialize + tools/list against the server as a subprocess.

    Catches startup-only breakage the in-process tests cannot see. It caught one: a module named
    `transport.py` became the package attribute `transport`, which mcp_framework reads as an optional
    transport factory, so the server died calling a module. Hence `http_client.py`.
    """
    from conftest import mcp_call

    async def action(session):
        return sorted(t.name for t in (await session.list_tools()).tools)

    server_dir = os.path.join(_CAP_DIR, "qwen_mm_plugins_mhs")
    assert set(mcp_call(server_dir, action)) == EXPECTED_TOOLS


def test_package_does_not_shadow_a_framework_hook():
    """Submodules land as package attributes, where mcp_framework looks for its optional hooks."""
    import qwen_mm_plugins_mhs.http_client  # noqa: F401 — must be imported to be an attribute

    for hook in ("transport", "on_start", "check_system", "launch_app"):
        attr = getattr(mhs, hook, None)
        assert not isinstance(attr, type(os)), f"module {hook!r} shadows the framework hook of that name"


def test_main_shim_is_byte_identical_to_the_template():
    """__main__.py is copied verbatim from the template and holds no per-server literals."""
    template = os.path.join(REPO_ROOT, "src", "capabilities", "example", "qwen_mm_plugins_example", "__main__.py")
    ours = os.path.join(_CAP_DIR, "qwen_mm_plugins_mhs", "__main__.py")
    with open(template, "rb") as a, open(ours, "rb") as b:
        assert a.read() == b.read()
