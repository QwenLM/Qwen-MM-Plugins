"""Render Office documents (DOCX, PPTX, VSDX) via LibreOffice -> PDF -> pypdfium2."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from shared.paths import path_to_file_uri
from shared.syscmd import which_tool


def _pdf_producer(path: str, soffice: str):
    """Build a produce(dest_pdf) closure that converts `path` via LibreOffice."""

    def _convert(dest_pdf: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Isolated LibreOffice user profile to avoid lock conflicts.
            profile_uri = path_to_file_uri(Path(tmpdir) / "lo_profile")
            profile = f"-env:UserInstallation={profile_uri}"
            cmd = [soffice, "--headless", "--norestore", profile, "--convert-to", "pdf", "--outdir", tmpdir, path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode} with no output"
                raise RuntimeError(f"LibreOffice conversion failed: {detail}")

            pdfs = [f for f in os.listdir(tmpdir) if f.endswith(".pdf")]
            if not pdfs:
                raise RuntimeError("LibreOffice produced no PDF output")

            shutil.copy2(os.path.join(tmpdir, pdfs[0]), dest_pdf)

    return _convert


def to_pdf(path: str, **opts: Any) -> str:
    """Convert an Office document to a cached intermediate PDF, returning its path."""
    from qwen_mm_plugins_core.renderers import cached_pdf

    soffice = which_tool("libreoffice") or which_tool("soffice")
    if not soffice:
        raise RuntimeError(
            "LibreOffice is required for this file type. Install: apt install libreoffice   |   brew install --cask libreoffice"
        )
    return cached_pdf(path, _pdf_producer(path, soffice))


def render(path: str, **opts: Any) -> list:
    from qwen_mm_plugins_core.renderers import render_via_pdf

    opts.setdefault("doc_type", os.path.splitext(path)[1].lstrip(".").upper() or "DOC")

    soffice = which_tool("libreoffice") or which_tool("soffice")
    if not soffice:
        if os.path.splitext(path)[1].lower() == ".xlsx":
            from qwen_mm_plugins_core.renderers.data import render as data_render

            return data_render(path, **opts)
        raise RuntimeError(
            "LibreOffice is required for this file type. Install: apt install libreoffice   |   brew install --cask libreoffice"
        )

    return render_via_pdf(path, _pdf_producer(path, soffice), **opts)
