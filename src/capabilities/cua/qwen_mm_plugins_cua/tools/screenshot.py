"""MCP tool (IMAGE return): capture a full-screen screenshot of the LOCAL machine.

macOS-first. Uses `mss` for cross-platform capture. This drives the machine the MCP
server runs on, so it needs a real display: it works on a local desktop, not on a
headless/remote box (there is no screen to grab).

macOS: the host process must hold Screen Recording permission
(System Settings > Privacy & Security > Screen Recording), else the grab is blank/fails.
Linux: X11 works; Wayland usually blocks raw screen grabs.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field

from shared.content import image, require_dep, text, text_error


class ScreenshotArgs(BaseModel):
    display: Annotated[
        int,
        Field(
            description=(
                "Which monitor to capture: 0 = all monitors stitched together, "
                "1 = primary, 2 = second, etc. Default 1 (primary)."
            ),
        ),
    ] = 1
    max_dimension: Annotated[
        int,
        Field(
            description=(
                "Downscale the inline preview so its longest side is at most this many pixels "
                "(keeps the response/token size sane). The file saved to disk stays full resolution. "
                "Default 1280."
            ),
        ),
    ] = 1280
    output_path: Annotated[
        Optional[str],
        Field(description="Where to save the full-resolution PNG. Defaults to a timestamped file in the OS temp dir."),
    ] = None


TOOL: dict[str, Any] = {
    "name": "screenshot",
    "description": (
        "Capture a full-screen screenshot of the local machine and return it as an image, "
        "plus save the full-resolution PNG to disk. Use this to see the current desktop state "
        "before deciding an action. Requires a real display on the machine running this server "
        "(macOS first) — it cannot capture a headless/remote server."
    ),
    "args": ScreenshotArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    if err := require_dep("mss", "mss"):
        return err
    if err := require_dep("PIL", "pillow"):
        return err

    import mss as mss_mod
    from PIL import Image

    display = int(arguments.get("display", 1))
    max_dim = max(64, int(arguments.get("max_dimension", 1280)))
    output_path = arguments.get("output_path")

    try:
        with mss_mod.mss() as sct:
            monitors = sct.monitors  # index 0 = all monitors stitched; 1..N = individual
            if display < 0 or display >= len(monitors):
                n = len(monitors) - 1
                return text_error(
                    f"display {display} not available; {n} monitor(s) detected "
                    f"(use 0 for all, 1..{n} for a specific one)"
                )
            raw = sct.grab(monitors[display])
    except Exception as e:  # noqa: BLE001 — surface actionable guidance rather than a raw traceback
        return text_error(_capture_hint(e))

    # mss returns raw BGRA; .rgb gives packed RGB bytes matching raw.size.
    img = Image.frombytes("RGB", raw.size, raw.rgb)
    full_w, full_h = img.size

    if not output_path:
        output_path = os.path.join(tempfile.gettempdir(), f"qmm_screenshot_{int(time.time())}.png")
    try:
        img.save(output_path, format="PNG")
    except OSError as e:
        return text_error(f"captured screen but could not save to {output_path}: {e}")

    # Downscale a copy for the inline preview to keep tokens/response size bounded.
    preview = img.copy()
    preview.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    preview.save(buf, format="JPEG", quality=70)

    return [
        text(
            f"screenshot: {full_w}x{full_h}px (display {display}), saved full-res PNG to {output_path}. "
            f"Inline preview downscaled to {preview.width}x{preview.height}. "
            f"Note: coordinates are in the full-res {full_w}x{full_h} space."
        ),
        image(buf.getvalue(), "image/jpeg"),
    ]


def _capture_hint(e: Exception) -> str:
    """Turn a capture failure into an actionable message (headless vs macOS permission vs Wayland)."""
    msg = str(e) or e.__class__.__name__
    headless_posix = (
        os.name == "posix"
        and sys.platform != "darwin"
        and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    )
    if headless_posix:
        return (
            f"screen capture failed ({msg}). No display detected (DISPLAY/WAYLAND_DISPLAY unset) — "
            "screenshot only works on a local machine with a real screen, not a headless/remote server. "
            "Run this server on the desktop you want to control (macOS first)."
        )
    if sys.platform == "darwin":
        return (
            f"screen capture failed ({msg}). On macOS, grant Screen Recording permission to the app "
            "running this server (System Settings > Privacy & Security > Screen Recording), then restart it."
        )
    return (
        f"screen capture failed ({msg}). On Linux/Wayland raw screen grab is often blocked — use an X11 "
        "session. On any OS, make sure a real display is attached."
    )
