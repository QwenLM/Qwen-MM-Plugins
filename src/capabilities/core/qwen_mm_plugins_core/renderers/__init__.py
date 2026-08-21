"""Renderer registry: maps file extensions to render functions."""

from __future__ import annotations

import importlib
import io
import os
from typing import Any, Callable

from shared.image import image_to_content as image_to_content
from shared.image import render_image_block as render_image_block

from . import code

# Default page/surface cap for multi-page renderers when the caller gives no `pages`.
DEFAULT_MAX_PAGES = 20

# ext -> (module_name_relative_to_this_package, function_name)
_REGISTRY: dict[str, tuple[str, str]] = {
    # PDF (pypdfium2) + SVG (resvg)
    ".pdf": ("pdf", "render"),
    ".svg": ("svg", "render"),
    # Office (LibreOffice -> PDF -> pypdfium2)
    ".docx": ("office", "render"),
    ".pptx": ("office", "render"),
    ".vsdx": ("office", "render"),
    # Data tables
    ".csv": ("data", "render"),
    ".xlsx": ("data", "render"),
    # Web / HTML
    ".html": ("web", "render"),
    ".htm": ("web", "render"),
    ".mhtml": ("web", "render"),
    # Code syntax highlighting
    ".css": ("code", "render"),
    ".js": ("code", "render"),
    ".ts": ("code", "render"),
    ".jsx": ("code", "render"),
    ".tsx": ("code", "render"),
    ".md": ("code", "render"),
    # DrawIO diagrams
    ".drawio": ("drawio", "render"),
    # Subtitles (returns text, not images)
    ".srt": ("subtitle", "render"),
    ".vtt": ("subtitle", "render"),
    # 3D models
    ".obj": ("model3d", "render"),
    ".stl": ("model3d", "render"),
    ".glb": ("model3d", "render"),
    ".gltf": ("model3d", "render"),
    ".fbx": ("model3d", "render"),
    ".ply": ("model3d", "render"),
    ".step": ("model3d", "render"),
    ".stp": ("model3d", "render"),
    # GIS / geospatial
    ".geojson": ("geo", "render"),
    ".kml": ("geo", "render"),
    ".shp": ("geo", "render"),
    # Medical imaging volumes
    ".nii": ("nifti", "render"),
    ".nii.gz": ("nifti", "render"),
    # Jupyter notebooks
    ".ipynb": ("notebook", "render"),
    # LaTeX
    ".tex": ("latex", "render"),
    # Plain text / logs — rendered as an unhighlighted fenced block by the code renderer
    ".txt": ("code", "render"),
    ".text": ("code", "render"),
    ".log": ("code", "render"),
}

# The code renderer highlights ~30 languages; register every one not already claimed by a
# more specific renderer (e.g. .tex → latex, .md → code) so visualize accepts a .py/.json/… file.
for _ext in code._EXT_TO_LANG:
    _REGISTRY.setdefault(_ext, ("code", "render"))

# Extensions handled by existing MCP tools (delegate, don't render)
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp", ".gif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".avi", ".mkv"})

SUPPORTED_EXTENSIONS = frozenset(_REGISTRY.keys()) | IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

_COMPOUND_EXTENSIONS = tuple(sorted((ext for ext in SUPPORTED_EXTENSIONS if ext.count(".") > 1), key=len, reverse=True))


def detect_extension(path: str) -> str:
    """Return a supported compound extension when present, else the final suffix."""
    lower_path = os.fspath(path).lower()
    for ext in _COMPOUND_EXTENSIONS:
        if lower_path.endswith(ext):
            return ext
    return os.path.splitext(lower_path)[1]


def get_renderer(ext: str) -> Callable[..., Any] | None:
    """Return the render function for a file extension, or None."""
    ext = ext.lower()
    spec = _REGISTRY.get(ext)
    if spec is None:
        return None
    mod_name, func_name = spec
    mod = importlib.import_module(f".{mod_name}", package=__name__)
    return getattr(mod, func_name)


def labeled_image(label: str, img, budget: str = "large") -> list[dict[str, Any]]:
    """A label with size info then the resized image block."""
    block, w, h = render_image_block(img, budget)
    return [{"type": "text", "text": f"{label} {h}x{w} (HxW)".strip()}, block]


def cached_pdf(src: str, produce: Callable[[str], None], cache_inputs: list[str] | None = None) -> str:
    """Return a cached intermediate-PDF path for `src`, producing it on a miss."""

    from shared.cache import cached_path

    out_pdf = cached_path(src, "pdf", extra_files=cache_inputs)
    if not os.path.isfile(out_pdf):
        produce(out_pdf)
    return out_pdf


def render_via_pdf(
    src: str, produce: Callable[[str], None], cache_inputs: list[str] | None = None, **opts: Any
) -> list[dict[str, Any]]:
    """Render `src` through a cached intermediate PDF.

    The cached PDF path is surfaced in output so the model can re-read other
    pages without re-running the conversion.
    """
    from qwen_mm_plugins_core.renderers.pdf import render as pdf_render

    out_pdf = cached_pdf(src, produce, cache_inputs)
    return pdf_render(out_pdf, cache_path=out_pdf, **opts)


# Extensions pypdfium2 opens directly (no intermediate conversion needed).
_PDF_NATIVE_EXTENSIONS = frozenset({".pdf"})


def source_to_pdf(path: str, **opts: Any) -> str | None:
    """Resolve `path` to a pypdfium2-openable PDF path, or None if unsupported.

    Native PDFs return as-is; Office/LaTeX return a cached intermediate PDF. SVG has no PDF
    form here (it rasterizes via resvg) — callers handle it separately.
    """

    ext = detect_extension(path)
    if ext in _PDF_NATIVE_EXTENSIONS:
        return path

    spec = _REGISTRY.get(ext)
    if spec is None:
        return None
    mod_name = spec[0]
    if mod_name in ("office", "latex"):
        mod = importlib.import_module(f".{mod_name}", package=__name__)
        return mod.to_pdf(path, **opts)
    return None


def fig_to_image(fig, pad_inches: float = 0.1):
    """Rasterize a matplotlib Figure to a PIL Image, closing the figure."""

    import matplotlib.pyplot as plt
    from PIL import Image

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", pad_inches=pad_inches)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf)


def parse_pages(pages_str: str, total_pages: int) -> list[int]:
    """Parse a page range string into 0-based page indices.

    Accepts: "1-5", "3", "1,3,5-8".  Input is 1-based, output is 0-based.
    """
    result = []
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = max(1, int(start_s.strip()))
            end = min(total_pages, int(end_s.strip()))
            result.extend(range(start - 1, end))
        else:
            idx = int(part.strip()) - 1
            if 0 <= idx < total_pages:
                result.append(idx)
    return sorted(set(result)) if result else list(range(min(total_pages, DEFAULT_MAX_PAGES)))
