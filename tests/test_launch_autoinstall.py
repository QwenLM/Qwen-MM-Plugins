"""Offline tests for the thin-client capabilities' rootless app auto-download + failure diagnosis.

The blender/freecad `--launch-app` (and QWEN_MM_AUTOLAUNCH) auto-download a pinned, relocatable app
build into the user cache when the binary is missing (Blender tarball / FreeCAD AppImage, Linux-x64,
no root). None of that touches the network here: we monkeypatch the download + AppImage-extract seams
and feed a synthetic tarball, so the real cache/extract/locate path runs hermetically.

We also cover the one prerequisite auto-download can't satisfy — a headless display (xvfb needs root)
— and assert that reason reaches the "could not connect" message a model would see.
"""

import hashlib
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import qwen_mm_plugins_blender.loader as bl_loader
import qwen_mm_plugins_freecad.loader as fc_loader
from qwen_mm_plugins_blender import _launch as bl_launch
from qwen_mm_plugins_freecad import _launch as fc_launch
from shared import applaunch

# ── auto-download: opt-out switch ────────────────────────────────────


def test_auto_install_on_by_default(monkeypatch):
    monkeypatch.delenv("QWEN_MM_NO_AUTO_INSTALL", raising=False)
    assert applaunch.auto_install_enabled() is True


@pytest.mark.parametrize("value", ["1", "on", " true "])
def test_auto_install_opt_out(monkeypatch, value):
    monkeypatch.setenv("QWEN_MM_NO_AUTO_INSTALL", value)
    assert applaunch.auto_install_enabled() is False


@pytest.mark.parametrize(("value", "expected"), [("1", True), (" On ", True), ("0", False), ("off", False)])
def test_autolaunch_reads_boolean_spellings(monkeypatch, value, expected):
    monkeypatch.setenv("QWEN_MM_AUTOLAUNCH", value)
    assert applaunch.autolaunch_enabled() is expected


def test_download_file_verifies_sha256_and_cleans_mismatch(monkeypatch, tmp_path):
    import urllib.request

    payload = b"trusted app bytes"

    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Response(payload))
    dest = tmp_path / "app.bin"
    applaunch.download_file(
        "https://downloads.example/app.bin",
        dest,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert dest.read_bytes() == payload

    dest.unlink()
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        applaunch.download_file("https://downloads.example/app.bin", dest, sha256="0" * 64)
    assert not dest.exists()
    assert not (tmp_path / "app.bin.part").exists()


# ── auto-download: blender tarball → extract → locate → cache ─────────


def test_ensure_blender_downloads_extracts_and_caches(monkeypatch, tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    monkeypatch.setattr(applaunch, "apps_cache_dir", lambda: apps)
    monkeypatch.setattr(bl_launch, "_blender_download_url", lambda: "http://example.invalid/b.tar.xz")

    downloaded = {}

    def fake_download(url, dest, **kw):
        downloaded.update(kw)
        # a real tar.xz whose top dir matches the pinned layout: blender-<v>-linux-x64/blender
        with tarfile.open(dest, "w:xz") as tf:
            data = b"#!/bin/sh\necho blender\n"
            ti = tarfile.TarInfo(f"blender-{bl_launch._BLENDER_VERSION}-linux-x64/blender")
            ti.size = len(data)
            ti.mode = 0o755
            tf.addfile(ti, io.BytesIO(data))

    monkeypatch.setattr(applaunch, "download_file", fake_download)

    binary = bl_launch._ensure_blender()
    assert binary is not None
    assert downloaded["sha256"] == bl_launch._BLENDER_LINUX_X64_SHA256
    assert Path(binary).is_file() and Path(binary).name == "blender"
    # the downloaded archive is cleaned up after extraction
    assert not (apps / f"blender-{bl_launch._BLENDER_VERSION}-linux-x64.tar.xz").exists()

    # cached: a second call must not re-download
    def boom(*a, **k):
        raise AssertionError("should not re-download when the binary is already cached")

    monkeypatch.setattr(applaunch, "download_file", boom)
    assert bl_launch._ensure_blender() == binary


def test_ensure_blender_none_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(bl_launch, "_blender_download_url", lambda: None)
    assert bl_launch._ensure_blender() is None


# ── auto-download: freecad AppImage → --appimage-extract → AppRun ─────


def test_ensure_freecad_downloads_extracts_and_caches(monkeypatch, tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    monkeypatch.setattr(applaunch, "apps_cache_dir", lambda: apps)
    monkeypatch.setattr(fc_launch, "_freecad_download_url", lambda: "http://example.invalid/f.AppImage")
    downloaded = {}

    def fake_download(url, dest, **kw):
        downloaded.update(kw)
        dest.write_bytes(b"AI\x02")

    monkeypatch.setattr(applaunch, "download_file", fake_download)

    def fake_extract(appimage, cwd):
        # mimic `--appimage-extract`: produce cwd/squashfs-root/AppRun
        apprun = cwd / "squashfs-root" / "AppRun"
        apprun.parent.mkdir(parents=True, exist_ok=True)
        apprun.write_text("#!/bin/sh\nexec freecad\n")
        return 0

    monkeypatch.setattr(fc_launch, "_appimage_extract", fake_extract)

    binary = fc_launch._ensure_freecad()
    assert binary is not None
    assert downloaded["sha256"] == fc_launch._FREECAD_LINUX_X64_SHA256
    assert Path(binary).is_file() and Path(binary).name == "AppRun"
    assert not (apps / fc_launch._FREECAD_APPIMAGE).exists()  # AppImage removed after extract

    def boom(*a, **k):
        raise AssertionError("should not re-download when AppRun is already cached")

    monkeypatch.setattr(applaunch, "download_file", boom)
    assert fc_launch._ensure_freecad() == binary


def test_ensure_freecad_none_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(fc_launch, "_freecad_download_url", lambda: None)
    assert fc_launch._ensure_freecad() is None


# ── launch_app wiring: auto-download triggers only when enabled ───────


def test_launch_app_invokes_autodownload_when_binary_missing(monkeypatch):
    monkeypatch.delenv("QWEN_MM_NO_AUTO_INSTALL", raising=False)
    monkeypatch.setattr(applaunch, "is_port_open", lambda *a, **k: False)
    monkeypatch.setattr(applaunch, "resolve_binary", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(bl_launch, "_ensure_blender", lambda: called.append(True) or None)
    rc = bl_launch.launch_app([])
    assert called == [True]  # auto-download attempted
    assert rc == 3  # still no binary → the documented "not found" exit code


def test_launch_app_skips_autodownload_when_opted_out(monkeypatch):
    monkeypatch.setenv("QWEN_MM_NO_AUTO_INSTALL", "1")
    monkeypatch.setattr(applaunch, "is_port_open", lambda *a, **k: False)
    monkeypatch.setattr(applaunch, "resolve_binary", lambda *a, **k: None)
    monkeypatch.setattr(fc_launch, "_platform_freecad_binaries", lambda: [])
    called = []
    monkeypatch.setattr(fc_launch, "_ensure_freecad", lambda: called.append(True) or None)
    rc = fc_launch.launch_app([])
    assert called == []  # opt-out respected — no download attempted
    assert rc == 3


# ── display diagnosis: xvfb is the one step auto-download can't do ────


def test_display_hint_when_xvfb_missing_and_headless(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(applaunch, "which_tool", lambda n: None)
    monkeypatch.delenv("DISPLAY", raising=False)
    hint = applaunch.autolaunch_display_hint()
    assert hint and "xvfb" in hint


def test_display_hint_none_when_xvfb_present(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(applaunch, "which_tool", lambda n: "/usr/bin/xvfb-run")
    assert applaunch.autolaunch_display_hint() is None


def test_native_desktop_does_not_require_xvfb(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(applaunch, "which_tool", lambda n: None)
    command = ["/Applications/Blender.app/Contents/MacOS/Blender"]
    assert applaunch.autolaunch_display_hint() is None
    assert applaunch.wrap_xvfb(command) is command


def test_blender_connect_error_surfaces_xvfb(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("BLENDER_HOST", "127.0.0.1")
    monkeypatch.setenv("BLENDER_PORT", "59999")  # nothing listening
    monkeypatch.delenv("QWEN_MM_AUTOLAUNCH", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(applaunch, "which_tool", lambda n: None)  # xvfb-run absent
    bl_loader._connection = None
    with pytest.raises(Exception) as ei:
        bl_loader.get_connection()
    assert "xvfb" in str(ei.value)


def test_freecad_connect_error_surfaces_xvfb(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("FREECAD_RPC_HOST", "127.0.0.1")
    monkeypatch.setenv("FREECAD_RPC_PORT", "59998")
    monkeypatch.delenv("QWEN_MM_AUTOLAUNCH", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(applaunch, "which_tool", lambda n: None)
    fc_loader._connection = None
    with pytest.raises(Exception) as ei:
        fc_loader.get_connection()
    assert "xvfb" in str(ei.value)


# ── FreeCAD desktop discovery + addon location ──────────────────────────────────────────────────────


def test_freecad_resolves_standard_desktop_install(monkeypatch, tmp_path):
    binary = tmp_path / "FreeCAD.app/Contents/MacOS/FreeCAD"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    monkeypatch.setattr(applaunch, "resolve_binary", lambda *a, **k: None)
    monkeypatch.setattr(fc_launch, "_platform_freecad_binaries", lambda: [binary])
    assert fc_launch._resolve_freecad_binary(None) == str(binary)


def test_freecad_mod_dir_uses_reported_user_app_data(monkeypatch, tmp_path):
    user_data = tmp_path / "FreeCAD/v1-1"
    monkeypatch.setattr(fc_launch, "get_env", lambda name, *a: None)
    monkeypatch.setattr(fc_launch, "_user_app_data", lambda binary: user_data)
    assert fc_launch._mod_dir(None, "/Applications/FreeCAD.app/Contents/MacOS/FreeCAD") == user_data / "Mod"


def test_freecad_queries_companion_command(monkeypatch, tmp_path):
    command = tmp_path / "freecadcmd"
    command.write_text("")
    expected = tmp_path / "FreeCAD/v1-1"
    monkeypatch.setattr(fc_launch, "_freecad_cmd_candidates", lambda binary: [command])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=f"{expected}\n", stderr=""),
    )
    assert fc_launch._user_app_data("freecad") == expected
