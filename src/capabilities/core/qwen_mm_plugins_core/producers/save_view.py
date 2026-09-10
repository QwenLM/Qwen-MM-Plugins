"""MCP tool: materialize views of a source (doc pages / video frames) as image files.

Supports PDF/SVG/XPS natively, PPTX/DOCX/XLSX/LaTeX via conversion (pages param),
and video frames at given timestamps (times param). Saves high-res files and returns
paths + previews for downstream tools (crop, grounding, ocr, etc.).
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from qwen_mm_plugins_core.renderers import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, labeled_image, parse_pages, source_to_pdf
from shared.content import require_file, text_error
from shared.env import MAX_RESPONSE_BYTES
from shared.image import render_pdf_page
from shared.video import parse_time


class SaveViewArgs(BaseModel):
    file_path: str
    pages: Optional[str] = None
    times: Optional[list[float | str]] = None
    dpi: Optional[int] = None
    budget: Literal["small", "normal", "large"] = "normal"
    output_dir: Optional[str] = None


TOOL = {"name": "save_view", "args": SaveViewArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize one or more views of a source as standalone image FILES and return their paths (+
    previews). For a document (PDF/SVG/XPS, and PPTX/DOCX/XLSX/LaTeX via conversion), pass `pages`
    (a range like '1', '1-5', '2,4,7') to render those pages/slides; for a video, pass `times` (a
    list of seconds) for those frames. Use this to turn document pages or video frames into image
    files you can then feed to crop / draw_bbox / grounding / ocr / image_search / segmentation.

    Args:
        file_path: Absolute path to the source document or video.
        pages: Page range for documents, e.g. '1', '1-5', '2,4,7'. Default: '1'. Ignored for video.
        times: Timestamps for video frames — seconds (52, 58.5) or a clock string
            ('MM:SS'/'HH:MM:SS'). Required for video. Ignored for docs.
        dpi: Render resolution for document pages (default 200). Ignored for video (native).
        budget: Resolution of the returned previews (the saved files are always full/native).
        output_dir: Directory to write the images to. Default: next to the source file.
    """
    file_path = arguments.get("file_path", "")
    if err := require_file(file_path):
        return err

    ext = os.path.splitext(file_path)[1].lower()
    budget = arguments.get("budget", "normal")
    out_dir = arguments.get("output_dir") or str(Path(file_path).parent)
    os.makedirs(out_dir, exist_ok=True)

    if ext in IMAGE_EXTENSIONS:
        return text_error(f"'{file_path}' is already an image — use crop / draw_bbox / grounding on it directly.")
    if ext in VIDEO_EXTENSIONS:
        return _save_frames(file_path, arguments.get("times"), budget, out_dir)
    return _save_pages(file_path, str(arguments.get("pages", "1")), arguments.get("dpi", 200), budget, out_dir)


def _save_pages(file_path: str, pages: str, dpi: int, budget: str, out_dir: str) -> list[dict[str, Any]]:
    stem = Path(file_path).stem

    # SVG rasterizes via resvg (no page concept) — save the single surface.
    if os.path.splitext(file_path)[1].lower() == ".svg":
        from shared.image import svg_to_image

        img = svg_to_image(svg_path=file_path, dpi=dpi)
        out = os.path.join(out_dir, f"{stem}.png")
        img.save(out, format="PNG")
        return _emit(f"Rendered {file_path} @ {dpi} DPI", [("[svg]", out, img)], budget)

    import pypdfium2 as pdfium

    # Office/LaTeX → cached intermediate PDF; native PDFs pass through.
    pdf_path = source_to_pdf(file_path)
    if pdf_path is None:
        return text_error(f"'{Path(file_path).suffix}' has no page view to materialize.")

    doc = pdfium.PdfDocument(pdf_path)
    try:
        total = len(doc)
        indices = parse_pages(pages, total)
        if not indices:
            return text_error(f"no pages selected from '{pages}' (document has {total} pages).")
        saved = []
        for idx in indices:
            img = render_pdf_page(doc[idx], dpi)
            out = os.path.join(out_dir, f"{stem}_page_{idx + 1}.png")
            img.save(out, format="PNG")
            saved.append((f"[page {idx + 1}]", out, img))
    finally:
        doc.close()

    return _emit(f"Rendered {len(saved)}/{total} page(s) of {file_path} @ {dpi} DPI", saved, budget)


def _save_frames(file_path: str, times: list[float] | None, budget: str, out_dir: str) -> list[dict[str, Any]]:
    if not times:
        return text_error("provide `times` (a list of seconds) to save video frame(s).")

    from PIL import Image

    from shared.video import extract_frames_by_seeking

    stem = Path(file_path).stem
    # target_h/w = 0 -> native resolution.
    frames = extract_frames_by_seeking(file_path, [parse_time(t) for t in times], 0, 0)
    if not frames:
        return text_error("no frames extracted (timestamps out of range?).")

    saved = []
    for ts, b64 in frames:
        raw = base64.b64decode(b64)
        out = os.path.join(out_dir, f"{stem}_frame_{ts:.1f}s.jpg")
        with open(out, "wb") as f:
            f.write(raw)
        saved.append((f"[{ts:.1f}s]", out, Image.open(io.BytesIO(raw))))

    return _emit(f"Extracted {len(saved)} frame(s) of {file_path} (native resolution)", saved, budget)


def _emit(header: str, saved: list[tuple[str, str, Any]], budget: str) -> list[dict[str, Any]]:
    """Build response: header + per-view previews, bounded by MAX_RESPONSE_BYTES."""
    listing = "\n".join(f"  {label} {img.height}x{img.width} -> {out}" for label, out, img in saved)
    blocks: list[dict[str, Any]] = [{"type": "text", "text": f"{header}:\n{listing}"}]

    used = 0
    for i, (label, _out, img) in enumerate(saved):
        label_block, image_block = labeled_image(label, img, budget)
        est = len(image_block["data"])
        if i and used + est > MAX_RESPONSE_BYTES:
            blocks.append({"type": "text", "text": f"... ({len(saved) - i} more preview(s) omitted; all files saved)"})
            break
        blocks.extend((label_block, image_block))
        used += est
    return blocks
