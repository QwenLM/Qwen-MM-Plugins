"""Locate required external CLI tools (ffmpeg/ffprobe/libreoffice/pdflatex/blender), recovering a
stunted login-shell PATH when a GUI/daemon launch drops the user's real PATH.

Part of the `shared` library (src/shared/) — stdlib-only. `--check-system`
(mcp_framework) reports the same tools from each server's SYSTEM_DEPS table; this is the
runtime locator with the PATH-recovery fallback.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# Required external CLI → OS-level install hint (pip/uv can't install these, so
# point at the OS package manager; `--check-system` lists them too).
_TOOL_INSTALL_HINT = {
    "ffmpeg": "apt install ffmpeg   |   brew install ffmpeg",
    "ffprobe": "apt install ffmpeg   |   brew install ffmpeg",
    "libreoffice": "apt install libreoffice   |   brew install --cask libreoffice",
    "pdflatex": "apt install texlive-latex-base texlive-latex-extra   |   brew install --cask basictex",
    "blender": "apt install blender   |   brew install --cask blender",
}

_path_recovered = False


def _recover_login_shell_path() -> None:
    """Merge the user's login-shell PATH into this process (once).

    When spawned by a GUI app or a launchd/systemd daemon, the server inherits a minimal PATH, so
    tools installed outside the system dirs (/opt/homebrew/bin, ~/.local/bin, …) go missing. Ask the
    login shell for the real PATH (`$SHELL -lic`). Skipped on Windows (PATH comes from the registry).
    """
    global _path_recovered
    if _path_recovered or os.name == "nt":
        return
    _path_recovered = True
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        result = subprocess.run(
            [shell, "-lic", 'printf %s "$PATH"'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra = [d for d in result.stdout.strip().split(os.pathsep) if d and d not in current]
    if extra:
        os.environ["PATH"] = os.pathsep.join(current + extra)


def which_tool(name: str) -> str | None:
    """Locate a CLI tool, recovering a stunted login-shell PATH if the first
    lookup fails (see _recover_login_shell_path). Returns the path, or None if
    the tool genuinely isn't installed. Use this when the caller has a fallback."""
    path = shutil.which(name)
    if path:
        return path
    _recover_login_shell_path()
    return shutil.which(name)


def find_tool(name: str) -> str:
    """which_tool(), but raise a clear error naming the OS package when the tool is genuinely
    missing — for required single tools (ffmpeg/ffprobe/pdflatex/blender)."""
    path = which_tool(name)
    if path:
        return path
    hint = _TOOL_INSTALL_HINT.get(name)
    suffix = f" — install: {hint}" if hint else ""
    raise FileNotFoundError(f"'{name}' not found{suffix}")
