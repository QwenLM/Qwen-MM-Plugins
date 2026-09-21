"""`qwen-mm-plugins-blender --launch-app` — bring up a live Blender carrying the bundled blender-mcp
addon, so the thin-client tools have a session to connect to.

The addon (`vendor/addon.py` + `vendor/blender_startup.py`) ships inside this package, so a
plugin-install user with no repo checkout can start the other half of the pipeline with one command:

    qwen-mm-plugins-blender --launch-app          # xvfb on Linux; native GUI on macOS/Windows
    qwen-mm-plugins-blender --launch-app --gui    # use the real Linux display

Wired via mcp_framework.run_main → this package's launch_app().
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from shared.env import get_env, get_int_env


def _vendor_dir() -> Path:
    return Path(__file__).resolve().parent / "vendor"


def _binary_candidates() -> list[str]:
    candidates = ["blender"]
    if sys.platform == "darwin":
        candidates.append("/Applications/Blender.app/Contents/MacOS/Blender")
    return candidates


# Pinned to the 4.2 LTS line the model trained against. The official Linux tarball is relocatable
# (extract anywhere, run in place — no root), so a plugin-install user with no Blender can get the
# exact trained version auto-provisioned. Auto-download is Linux-x86_64 only; elsewhere fall back to
# the manual install hint. Bump the patch as new 4.2.x releases land (see download.blender.org).
_BLENDER_VERSION = "4.2.23"
_BLENDER_SERIES = "4.2"
# Published by Blender alongside the pinned release:
# https://download.blender.org/release/Blender4.2/blender-4.2.23.sha256
_BLENDER_LINUX_X64_SHA256 = "bea0eb3146be13eae6225409a117b215184f41b7f79e799f97cb3abb8f6dc404"


def _blender_download_url() -> str | None:
    import platform

    if sys.platform != "linux" or platform.machine() not in ("x86_64", "amd64"):
        return None
    return f"https://download.blender.org/release/Blender{_BLENDER_SERIES}/blender-{_BLENDER_VERSION}-linux-x64.tar.xz"


def _ensure_blender() -> str | None:
    """Return a usable blender binary, auto-downloading the pinned tarball into the user cache
    (rootless) if it isn't already there. Linux-x86_64 only; returns None if it can't be provided."""
    from shared import applaunch

    url = _blender_download_url()
    if not url:
        return None  # unsupported platform — caller prints the manual hint
    apps = applaunch.apps_cache_dir()
    binary = apps / f"blender-{_BLENDER_VERSION}-linux-x64" / "blender"
    if binary.is_file():
        return str(binary)

    tarball = apps / f"blender-{_BLENDER_VERSION}-linux-x64.tar.xz"
    print(
        f"Blender not found — auto-downloading {_BLENDER_VERSION} (~300 MB, one-time) into {apps} …\n"
        "  (set QWEN_MM_NO_AUTO_INSTALL=1 to disable, or BLENDER_BINARY to point at your own)",
        file=sys.stderr,
    )
    try:
        if tarball.is_file() and not applaunch.verify_file_sha256(tarball, _BLENDER_LINUX_X64_SHA256):
            tarball.unlink()
        if not tarball.is_file():
            applaunch.download_file(url, tarball, sha256=_BLENDER_LINUX_X64_SHA256)
        import tarfile

        with tarfile.open(tarball) as tf:
            try:
                tf.extractall(apps, filter="data")
            except TypeError:  # Python 3.10/3.11
                tf.extractall(apps)
    except Exception as e:
        print(
            f"  auto-download failed: {e}\n  install Blender manually (see --check-system) or set BLENDER_BINARY.",
            file=sys.stderr,
        )
        return None
    finally:
        tarball.unlink(missing_ok=True)

    if binary.is_file():
        binary.chmod(0o755)
        print(f"  Blender {_BLENDER_VERSION} ready at {binary}", file=sys.stderr)
        return str(binary)
    return None


def launch_app(argv: list[str]) -> int:
    from shared import applaunch

    ap = argparse.ArgumentParser(
        prog="qwen-mm-plugins-blender --launch-app",
        description="Start a live Blender + blender-mcp addon for the MCP tools to connect to.",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=get_int_env("BLENDER_PORT", 9876),
        help="TCP port for the addon server (default: $BLENDER_PORT or 9876)",
    )
    ap.add_argument(
        "--gui",
        action="store_true",
        help="use the real Linux display (default: xvfb on Linux; native GUI on macOS/Windows)",
    )
    ap.add_argument(
        "--foreground", action="store_true", help="block in the foreground instead of detaching once the port is up"
    )
    ap.add_argument(
        "--binary",
        default=get_env("BLENDER_BINARY") or None,
        help="path to the blender binary (default: $BLENDER_BINARY, else search PATH)",
    )
    ap.add_argument("--wait", type=float, default=90.0, help="seconds to wait for the port after launch (default: 90)")
    args = ap.parse_args(argv)

    host = get_env("BLENDER_HOST", "127.0.0.1")
    if applaunch.is_port_open(host, args.port):
        # status → stderr: launch_app is reused on the in-server autolaunch path, where stdout
        # is the MCP stdio JSON-RPC channel; a stray line there corrupts the protocol stream.
        print(f"Blender MCP already listening on {host}:{args.port} — nothing to do.", file=sys.stderr)
        return 0

    binary = applaunch.resolve_binary(_binary_candidates(), explicit=args.binary)
    if not binary and applaunch.auto_install_enabled():
        binary = _ensure_blender()
    if not binary:
        print(
            "blender not found — install it, then retry (see --check-system).\n"
            "  apt install blender  |  brew install --cask blender\n"
            "  (auto-download covers Linux-x86_64; QWEN_MM_NO_AUTO_INSTALL disables it)",
            file=sys.stderr,
        )
        return 3

    vendor = _vendor_dir()
    addon, startup = vendor / "addon.py", vendor / "blender_startup.py"
    for f in (addon, startup):
        if not f.is_file():
            print(f"bundled addon missing: {f} (reinstall the plugin)", file=sys.stderr)
            return 4

    env = {**os.environ, "BLENDER_PORT": str(args.port), "BLENDER_MCP_ADDON_PATH": str(addon)}
    cmd = [binary, "--python", str(startup)]
    if not args.gui:
        try:
            cmd = applaunch.wrap_xvfb(cmd)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 3

    if args.foreground:
        print(f"Launching Blender (foreground) on port {args.port} …", file=sys.stderr)
        return applaunch.run_foreground(cmd, env=env)

    log = applaunch.default_log_path("blender")
    proc = applaunch.spawn_detached(cmd, env=env, log_path=log)
    headless = not args.gui and sys.platform == "linux"
    print(
        f"Launching Blender (pid {proc.pid}, headless={'yes' if headless else 'no'}) on {host}:{args.port}; log: {log}",
        file=sys.stderr,
    )
    if applaunch.wait_for_port(host, args.port, timeout=args.wait):
        print(f"Blender MCP ready on {host}:{args.port}.", file=sys.stderr)
        return 0
    print(f"Blender did not open {host}:{args.port} within {args.wait:.0f}s — see {log}.", file=sys.stderr)
    return 5
