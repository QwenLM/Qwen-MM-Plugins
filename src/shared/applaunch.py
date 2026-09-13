"""Helpers for the thin-client capabilities that can bring up their own live app.

The blender/freecad MCP servers are thin clients: they talk to a *running* Blender/FreeCAD that
carries a vendored addon. Normally the user starts that app themselves, but each server also exposes
a `--launch-app` subcommand (wired in `mcp_framework.run_main` → the package's `launch_app()`), so a
plugin-install user with no repo checkout can still get the other half of the pipeline up with one
command. This module is the shared, stdlib-only plumbing those launchers compose:

  - is_port_open / wait_for_port   — TCP readiness (the app's RPC port)
  - resolve_binary                 — locate the app binary on PATH (with login-shell PATH recovery)
  - auto_install_enabled / apps_cache_dir / download_file — rootless auto-download of a missing app
  - autolaunch_display_hint        — Linux-only xvfb prerequisite diagnosis
  - wrap_xvfb                      — use xvfb on Linux; launch the native GUI elsewhere
  - spawn_detached / run_foreground — start the app, backgrounded (return once ready) or blocking
  - autolaunch_enabled / is_local_host / launch_and_probe / startup_probe — the client-side
    autolaunch-on-first-connect + startup-probe logic shared by the blender/freecad loaders

Part of the `shared` library (src/shared/) — no third-party deps (stdlib urllib for the download,
since the blender/freecad extras declare no pip deps of their own).
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .syscmd import which_tool

log = logging.getLogger("qwen-mm-plugins.applaunch")

__all__ = [
    "is_port_open",
    "wait_for_port",
    "resolve_binary",
    "auto_install_enabled",
    "apps_cache_dir",
    "download_file",
    "verify_file_sha256",
    "autolaunch_display_hint",
    "wrap_xvfb",
    "spawn_detached",
    "run_foreground",
    "default_log_path",
    "autolaunch_enabled",
    "is_local_host",
    "launch_and_probe",
    "startup_probe",
]


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """True if something is accepting TCP connections at host:port right now."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(host: str, port: int, timeout: float = 60.0, interval: float = 0.5) -> bool:
    """Poll host:port until it accepts a connection or `timeout` seconds elapse. Returns readiness."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_port_open(host, port, timeout=min(interval, 1.0)):
            return True
        time.sleep(interval)
    return is_port_open(host, port, timeout=1.0)


def resolve_binary(candidates: list[str], explicit: str | None = None) -> str | None:
    """Resolve the app binary: an explicit path (file or on PATH) wins, else the first `candidates`
    entry that resolves on PATH (recovering a stunted login-shell PATH — see shared.syscmd)."""
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return str(p)
        return which_tool(explicit)
    for name in candidates:
        found = which_tool(name)
        if found:
            return found
    return None


# ── rootless auto-download of a missing app ──────────────────────────
#
# A plugin-install user often has no Blender/FreeCAD on the box (and, if they installed via a distro,
# a version too old for what the model trained on). Both apps ship a relocatable Linux-x86_64 build
# that extracts anywhere with no root (Blender: a tarball; FreeCAD: an AppImage we `--appimage-extract`
# to be FUSE-free), so the launchers can fetch a pinned build into the user cache. The app-specific
# URLs + layout live in each capability's `_launch.py`; the mechanics are here. The one prerequisite
# this can NOT satisfy on headless Linux is a display: xvfb needs root to install (see
# autolaunch_display_hint). Other desktop platforms launch the native GUI directly.


def auto_install_enabled() -> bool:
    """Whether a missing app may be auto-downloaded. On by default; set QWEN_MM_NO_AUTO_INSTALL=1 on a
    managed box that provisions the binaries itself (or to avoid a large download)."""
    from .env import get_bool_env

    return not get_bool_env("QWEN_MM_NO_AUTO_INSTALL")


def apps_cache_dir() -> Path:
    """Directory under the user cache where auto-downloaded app builds are extracted (created on
    demand). Shares shared.cache.cache_dir(), so QWEN_MM_CACHE relocates it too."""
    from .cache import cache_dir

    d = Path(cache_dir()) / "apps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def verify_file_sha256(path: Path, expected: str) -> bool:
    """Return whether `path` matches an expected SHA-256 digest."""
    import hashlib

    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected.lower()


def download_file(url: str, dest: Path, *, sha256: str, timeout: float = 60.0) -> None:
    """Stream `url` to `dest` with stdlib urllib (no `requests` — the blender/freecad extras have no
    pip deps). Requires the pinned release digest, writes to a `.part` sidecar, and renames only after
    it matches, so a partial or substituted download never looks complete. Logs coarse progress to
    stderr (large builds take a while)."""
    import hashlib
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "qwen-mm-plugins"})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (pinned official URL)
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            next_mark = 25 * 1024 * 1024  # log roughly every 25 MB
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if done >= next_mark:
                        pct = f" ({done * 100 // total}%)" if total else ""
                        print(f"    … {done // (1024 * 1024)} MB{pct}", file=sys.stderr)
                        next_mark += 25 * 1024 * 1024
        if digest.hexdigest() != sha256.lower():
            raise ValueError(f"SHA-256 mismatch for {dest.name}")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def autolaunch_display_hint() -> str | None:
    """Actionable hint when a Linux headless autolaunch can't get a display, else None.

    Linux autolaunch always starts the app under xvfb, so a missing `xvfb-run` blocks it even on a box
    that happens to have a real $DISPLAY. Other platforms launch the native GUI directly, so they
    must never receive a Linux install hint."""
    if sys.platform != "linux":
        return None
    if which_tool("xvfb-run"):
        return None
    if os.environ.get("DISPLAY"):
        return (
            "autolaunch runs headless and needs xvfb-run — `sudo apt install xvfb`, or start the "
            "app on your display yourself with `--launch-app --gui`"
        )
    return (
        "this headless box has no virtual display — `sudo apt install xvfb` (one-time, needs "
        "root; the only step auto-download can't do for you), then retry"
    )


def wrap_xvfb(cmd: list[str], *, screen: str = "1920x1080x24") -> list[str]:
    """Use xvfb for a Linux GUI app; return `cmd` unchanged elsewhere.

    Raises FileNotFoundError (with an install hint) when xvfb-run is unavailable on Linux. Native
    desktop platforms do not provide xvfb and should launch their normal GUI executable directly.
    """
    if sys.platform != "linux":
        return cmd
    xvfb = which_tool("xvfb-run")
    if not xvfb:
        raise FileNotFoundError(
            "'xvfb-run' not found — needed to run the GUI app headless. install: apt install xvfb "
            "(or pass --gui on a machine with a real display)"
        )
    return [xvfb, "-a", "-s", f"-screen 0 {screen}", *cmd]


def default_log_path(name: str) -> Path:
    """A stable per-app log file under the shared cache dir (created on demand).

    Routes through shared.cache.cache_dir() like apps_cache_dir(), so QWEN_MM_CACHE relocates the
    log alongside the downloaded app build instead of leaving it under ~/.cache."""
    from .cache import cache_dir

    return Path(cache_dir()) / f"{name}.log"


def spawn_detached(cmd: list[str], *, env: dict | None = None, log_path: Path | None = None) -> subprocess.Popen:
    """Start `cmd` in its own session (survives this process), with stdout+stderr appended to
    `log_path`. Returns the Popen so the caller can report the pid; it does NOT wait."""
    logf = open(log_path, "ab") if log_path else subprocess.DEVNULL  # noqa: SIM115 (closed below)
    kwargs: dict = {"stdout": logf, "stderr": subprocess.STDOUT, "env": env}
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    finally:
        # The child inherits its own dup of the fd; the (possibly long-lived) parent doesn't need
        # to keep this one open — don't rely on GC to close it.
        if log_path:
            logf.close()


def run_foreground(cmd: list[str], *, env: dict | None = None) -> int:
    """Run `cmd` in the foreground, forwarding output to this process's stdout/stderr. Returns the
    exit code (or 130 on Ctrl-C)."""
    try:
        return subprocess.call(cmd, env=env, stdout=sys.stderr, stderr=sys.stderr)
    except KeyboardInterrupt:
        return 130


# ── client-side autolaunch-on-first-connect (shared by the blender/freecad loaders) ──────
#
# A thin-client loader connects to a *running* app. When QWEN_MM_AUTOLAUNCH is set it may bring the
# app up on the first tool call (deferred off the startup path, which runs before the MCP handshake).
# The predicates + launch/probe bodies are identical across capabilities; only the app label and the
# once-per-process gate are capability-local, so the loader keeps the gate and composes these.


def autolaunch_enabled() -> bool:
    """Whether QWEN_MM_AUTOLAUNCH opts into launching the app on first connect."""
    from .env import get_bool_env

    return get_bool_env("QWEN_MM_AUTOLAUNCH")


def is_local_host(host: str) -> bool:
    """Whether `host` is the local machine — autolaunch only manages a local app; a remote host is
    the user's to run."""
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def launch_and_probe(host: str, port: int, launch_fn, *, app: str) -> bool:
    """Run `launch_fn([])` to bring up `app`, then report whether host:port is open. The caller has
    already checked autolaunch_enabled()/is_local_host() and its once-per-process gate."""
    log.warning("QWEN_MM_AUTOLAUNCH set — launching %s + the bundled addon on %s:%s …", app, host, port)
    try:
        launch_fn([])  # blocks until the port is up (or times out), then detaches
    except Exception as e:
        log.warning("autolaunch failed: %s", e)
    return is_port_open(host, port)


def startup_probe(host: str, port: int, *, app: str, entry: str) -> None:
    """Fast, non-blocking connectivity check for a loader's on_start().

    Deliberately does NOT connect or autolaunch (launching can take tens of seconds and on_start runs
    before the MCP initialize handshake): it only logs an actionable state."""
    if is_port_open(host, port):
        log.info("%s reachable on %s:%s at startup", app, host, port)
    elif autolaunch_enabled():
        log.warning(
            "%s not reachable on %s:%s — QWEN_MM_AUTOLAUNCH is set, so it will be started on the first tool call.",
            app,
            host,
            port,
        )
    else:
        log.warning(
            "%s not reachable on %s:%s. Start it with `%s --launch-app` before using tools, "
            "or set QWEN_MM_AUTOLAUNCH=1 (see SKILL.md).",
            app,
            host,
            port,
            entry,
        )
