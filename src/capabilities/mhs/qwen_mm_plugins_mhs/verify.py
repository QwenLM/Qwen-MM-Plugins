"""Conformance checker for a candidate MHS adapter — run it before trusting one.

    python3 -m qwen_mm_plugins_mhs.verify http://127.0.0.1:8800

Exercises the protocol against a live adapter and reports, per check, exactly what is wrong rather
than leaving a subtly non-conformant adapter to surface as vague tool failures later. Written for the
case where an adapter was just authored — including by a model — and nobody has reviewed it.

STRICTLY READ-ONLY. It never calls write, and never calls reset: on real hardware those move things or
stop them, and a checker that actuates equipment to prove it can actuate equipment is not a checker.
Write and reset capabilities are validated by their *declaration* only, and the report says so.

Validation deliberately runs through this package's own protocol module — the same normalize_meta /
to_content_blocks / check_* the server uses. A checker with its own private copy of the rules would
drift from the thing that actually enforces them, and then agree with nobody.
"""

from __future__ import annotations

from typing import Any

from .config import DEFAULT_BASE_PATH, DEFAULT_TIMEOUT, Adapter
from .http_client import AdapterError, request_json, segment
from .protocol import ProtocolError, normalize_device_summary, normalize_meta, to_content_blocks

# A device that reports one of these is legible to the model; anything else still works but reads as
# an unknown word in the health report.
_CONVENTIONAL_STATES = {"online", "busy", "offline", "error", "maintenance", "unknown"}

# Asking for a device that cannot exist is how the error contract gets checked without touching
# hardware: the adapter should answer 404 with an error object.
_ABSENT_DEVICE = "__mhs_verify_absent_device__"


class Report:
    """Collected findings. `failed` decides the exit code; warnings do not."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = 0
        self.warned = 0
        self.passed = 0

    def ok(self, what: str) -> None:
        self.passed += 1
        self.lines.append(f"  ✓ {what}")

    def warn(self, what: str, fix: str = "") -> None:
        self.warned += 1
        self.lines.append(f"  ! {what}")
        if fix:
            self.lines.append(f"      {fix}")

    def fail(self, what: str, fix: str = "") -> None:
        self.failed += 1
        self.lines.append(f"  ✗ {what}")
        if fix:
            self.lines.append(f"      {fix}")

    def section(self, title: str) -> None:
        self.lines.append("")
        self.lines.append(title)

    def render(self) -> str:
        verdict = (
            f"FAIL — {self.failed} problem(s), {self.warned} warning(s)"
            if self.failed
            else f"PASS — {self.passed} check(s), {self.warned} warning(s)"
        )
        return "\n".join([*self.lines, "", verdict])


def _check_device_list(adapter: Adapter, report: Report) -> list[dict[str, Any]]:
    report.section("GET /devices")
    try:
        payload = request_json(adapter, "GET", "/devices")
    except AdapterError as exc:
        report.fail(f"unreachable: {exc}", "Is the adapter running, and is the URL right?")
        return []

    raw = payload.get("devices")
    if not isinstance(raw, list):
        report.fail(
            "response has no 'devices' list",
            'Return {"devices": [{"device_id": "...", ...}]} — see adapter_protocol.md.',
        )
        return []

    summaries = [s for s in (normalize_device_summary(e, adapter.name) for e in raw) if s is not None]
    if len(summaries) != len(raw):
        report.fail(
            f"{len(raw) - len(summaries)} of {len(raw)} entries have no usable 'device_id' and were dropped",
            "Every entry needs a non-empty string device_id.",
        )
    if not summaries:
        report.fail("no usable devices reported", "An adapter with no devices cannot be used.")
        return []

    report.ok(f"{len(summaries)} device(s): {', '.join(s['adapter_device_id'] for s in summaries)}")
    for summary in summaries:
        if not summary["capabilities"]:
            report.warn(
                f"{summary['adapter_device_id']}: no 'capabilities' in the device list",
                "Include capability names here so the model sees them without a metadata call each.",
            )
        if summary["device_type"] == "unknown":
            report.warn(f"{summary['adapter_device_id']}: no 'device_type'")
    return summaries


def _check_meta(adapter: Adapter, device: str, report: Report) -> dict[str, Any] | None:
    report.section(f"GET /devices/{device}")
    try:
        raw = request_json(adapter, "GET", f"/devices/{segment(device)}")
    except AdapterError as exc:
        report.fail(f"metadata unavailable: {exc}")
        return None
    try:
        meta = normalize_meta(raw, f"{adapter.name}/{device}")
    except ProtocolError as exc:
        report.fail(str(exc))
        return None

    if not meta["capabilities"]:
        report.fail(
            "no capabilities declared",
            "Without capabilities the device can be neither read nor commanded.",
        )
    else:
        report.ok(f"capabilities: {', '.join(c['name'] for c in meta['capabilities'])}")
    if not meta["description"]:
        report.warn(
            "no 'description'",
            "This is the sentence the model reads to decide what the device is. Write one.",
        )
    else:
        report.ok("has a natural-language description")

    # normalize_meta collapses "explicitly both" and "missing/invalid, so defaulted to both" into the
    # same value, so read the RAW field: an honest bidirectional capability must not be nagged, while a
    # forgotten direction silently permits writes to hardware and must be.
    for name, declared in _raw_directions(raw).items():
        if declared not in ("read", "write", "both"):
            shown = "absent" if declared is _MISSING else repr(declared)
            report.warn(
                f"{name!r}: no valid 'direction' ({shown}) — defaulted to 'both', which lets a write "
                "reach the hardware",
                "Declare 'read' for telemetry. A read-only declaration is host-enforced, so it makes "
                "an accidental write unreachable and costs nothing to widen later.",
            )

    _check_limits(meta, report)
    return meta


_MISSING = object()


def _raw_directions(raw: object) -> dict[str, Any]:
    """Capability name -> the `direction` exactly as the adapter sent it (_MISSING when absent)."""
    out: dict[str, Any] = {}
    entries = raw.get("capabilities") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            out[entry.strip()] = _MISSING  # bare-string shorthand declares no direction
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"].strip():
            out[entry["name"].strip()] = entry.get("direction", _MISSING)
    return out


def _check_limits(meta: dict[str, Any], report: Report) -> None:
    writable = [c for c in meta["capabilities"] if c["direction"] in ("write", "both")]
    limited = {limit["parameter"] for limit in meta["safety_limits"]}

    for limit in meta["safety_limits"]:
        if limit["min"] is None and limit["max"] is None:
            report.fail(
                f"safety limit on {limit['parameter']!r} has neither 'min' nor 'max'",
                "A limit with no bound enforces nothing. Give at least one.",
            )
        elif not limit["unit"]:
            report.warn(f"safety limit on {limit['parameter']!r} has no 'unit'")

    if meta["safety_limits"]:
        hard = sum(1 for limit in meta["safety_limits"] if limit["hard"])
        report.ok(f"{len(meta['safety_limits'])} safety limit(s), {hard} hard")
    elif writable:
        report.warn(
            f"{len(writable)} writable capability/capabilities but no safety_limits",
            "The host enforces exactly what you declare, so this is the cheapest place to make an "
            "unsafe command impossible. If the device genuinely has no bounds, say so in the description.",
        )

    for cap in writable:
        params = cap["params"] if isinstance(cap["params"], dict) else {}
        unbounded = [p for p in params if p not in limited]
        if unbounded and not cap["requires_confirm"]:
            report.warn(
                f"{cap['name']!r} writes {', '.join(sorted(unbounded))} with no declared limit and no requires_confirm",
                "Either bound the parameter or set requires_confirm: true.",
            )


def _check_health(adapter: Adapter, device: str, report: Report) -> None:
    report.section(f"GET /devices/{device}/health")
    try:
        payload = request_json(adapter, "GET", f"/devices/{segment(device)}/health")
    except AdapterError as exc:
        report.fail(f"health unavailable: {exc}", "Health is how the model checks a device before retrying.")
        return
    state = payload.get("state")
    if not isinstance(state, str) or not state:
        report.fail("health response has no 'state'")
    elif state not in _CONVENTIONAL_STATES:
        report.warn(
            f"state {state!r} is not one of {'/'.join(sorted(_CONVENTIONAL_STATES - {'unknown'}))}",
            "Non-standard states still work but are harder for the model to act on.",
        )
    else:
        report.ok(f"state {state!r}")
    if not isinstance(payload.get("healthy"), bool):
        report.warn("no boolean 'healthy'", "The model uses this for a quick go/no-go.")


def _check_reads(adapter: Adapter, device: str, meta: dict[str, Any], report: Report) -> None:
    readable = [c["name"] for c in meta["capabilities"] if c["direction"] in ("read", "both")]
    report.section(f"POST /devices/{device}/read/<capability>  ({len(readable)} readable)")
    for name in readable:
        try:
            payload = request_json(adapter, "POST", f"/devices/{segment(device)}/read/{segment(name)}", {})
        except AdapterError as exc:
            report.fail(f"read {name!r} failed: {exc}")
            continue
        if "blocks" not in payload:
            report.fail(
                f"read {name!r} returned no 'blocks' key",
                'Return {"blocks": [...]} even when there is one value.',
            )
            continue
        try:
            blocks = to_content_blocks(payload["blocks"], f"{device}.{name}")
        except ProtocolError as exc:
            report.fail(f"read {name!r}: {exc}")
            continue
        if not blocks:
            report.warn(f"read {name!r} returned an empty block list")
            continue
        degraded = [b for b in blocks if b["type"] == "text" and b["text"].startswith("[")]
        kinds = ", ".join(sorted({b["type"] for b in blocks}))
        if degraded:
            report.fail(
                f"read {name!r}: a block could not be used — {degraded[0]['text']}",
                "Most often a malformed image block (bad base64, or a path this host cannot see).",
            )
        else:
            report.ok(f"read {name!r} → {len(blocks)} block(s) [{kinds}]")


def _check_error_contract(adapter: Adapter, report: Report) -> None:
    report.section("error handling (no hardware touched)")
    try:
        request_json(adapter, "GET", f"/devices/{_ABSENT_DEVICE}")
    except AdapterError as exc:
        if exc.status == 404:
            report.ok(f"unknown device → HTTP 404{f' [{exc.code}]' if exc.code else ''}")
            if not exc.code:
                report.warn(
                    "the 404 body had no error.code",
                    'Return {"error": {"code": "...", "message": "..."}}.',
                )
        elif exc.status is None:
            report.fail(f"unknown device produced no HTTP response: {exc}")
        else:
            report.warn(
                f"unknown device → HTTP {exc.status}, expected 404",
                "Use real status codes so the host can tell 'wrong name' from 'device broken'.",
            )
        return
    report.fail(
        "a request for a nonexistent device succeeded",
        "Unknown device ids must be rejected with 404, not answered.",
    )


def _report_unexercised(meta_by_device: dict[str, dict[str, Any]], report: Report) -> None:
    report.section("not exercised (would actuate hardware)")
    writes = sorted(
        f"{device}.{cap['name']}"
        for device, meta in meta_by_device.items()
        for cap in meta["capabilities"]
        if cap["direction"] in ("write", "both")
    )
    report.lines.append(
        f"  write: {', '.join(writes) if writes else 'none declared'}"
        "\n      Declarations were checked; the commands were NOT sent."
    )
    report.lines.append(
        "  reset: NOT called — on real hardware reset/estop stops the device."
        "\n      Test it yourself, deliberately, when it is safe to do so."
    )


def verify(url: str, *, timeout: float = DEFAULT_TIMEOUT, base_path: str = DEFAULT_BASE_PATH) -> Report:
    """Run every read-only conformance check against the adapter at `url`."""
    adapter = Adapter(name="candidate", url=url.rstrip("/"), timeout=timeout, base_path=base_path)
    report = Report()
    report.lines.append(f"Verifying MHS adapter at {adapter.url}{adapter.base_path} (read-only)")

    summaries = _check_device_list(adapter, report)
    meta_by_device: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        device = summary["adapter_device_id"]
        meta = _check_meta(adapter, device, report)
        if meta is None:
            continue
        meta_by_device[device] = meta
        _check_health(adapter, device, report)
        _check_reads(adapter, device, meta, report)

    if summaries:
        _check_error_contract(adapter, report)
        _report_unexercised(meta_by_device, report)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m qwen_mm_plugins_mhs.verify",
        description="Check a candidate MHS adapter against MHS-HTTP/1. Read-only: never writes or resets.",
    )
    parser.add_argument("url", help="adapter base URL, e.g. http://127.0.0.1:8800")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-request seconds")
    parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help=f"default {DEFAULT_BASE_PATH}")
    args = parser.parse_args(argv)

    report = verify(args.url, timeout=args.timeout, base_path=args.base_path)
    print(report.render())
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
