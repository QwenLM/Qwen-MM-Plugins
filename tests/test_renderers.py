"""Renderer matrix: assert visualize.handle() renders each supported format.

Parametrized pytest; a case whose optional dependency or asset is missing is skipped.
"""

import base64
import io
import os
import shutil
import sys

import pytest

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from qwen_mm_plugins_core.visualizers.visualize import handle  # noqa: E402  (import after sys.path setup)


def test_windows_3d_uses_isolated_worker(monkeypatch):
    from qwen_mm_plugins_core.renderers import model3d

    expected = [{"type": "image", "data": "encoded", "mimeType": "image/png"}]
    monkeypatch.setattr(model3d, "_WINDOWS", True)
    monkeypatch.setattr(model3d, "_render_pyrender_subprocess", lambda path, max_pages, budget: expected)
    monkeypatch.setattr(
        model3d,
        "render_blender",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Windows must not launch Blender")),
    )
    monkeypatch.setattr(
        model3d,
        "_load_scene",
        lambda *args: (_ for _ in ()).throw(AssertionError("Windows parent must not load native render dependencies")),
    )

    assert model3d.render("sample.stl", max_pages=1, budget="small") == expected


def _has_dep(key: str) -> bool:
    checks = {
        "pypdfium2": lambda: __import__("pypdfium2"),
        "resvg_py": lambda: __import__("resvg_py"),
        "lxml": lambda: __import__("lxml"),
        "pandas": lambda: __import__("pandas"),
        "matplotlib": lambda: __import__("matplotlib"),
        "openpyxl": lambda: __import__("openpyxl"),
        "nbformat": lambda: __import__("nbformat"),
        "geopandas": lambda: __import__("geopandas"),
        "trimesh": lambda: __import__("trimesh"),
        "libreoffice": lambda: shutil.which("libreoffice") or shutil.which("soffice"),
        "pdflatex": lambda: shutil.which("pdflatex"),
        "blender": lambda: shutil.which("blender"),
    }
    try:
        return bool(checks[key]())
    except Exception:
        return False


# (file, expected_type, min_items, description, requires) — one representative asset
# per format family (the render path is the same regardless of which sample).
TESTS = [
    ("sample.pdf", "image", 2, "PDF (pypdfium2)", ["pypdfium2"]),
    ("sample.svg", "image", 1, "SVG (resvg)", ["resvg_py"]),
    ("sample.docx", "image", 1, "DOCX (LibreOffice)", ["libreoffice", "pypdfium2"]),
    ("sample.pptx", "image", 1, "PPTX (LibreOffice)", ["libreoffice", "pypdfium2"]),
    ("sample.csv", "image", 1, "CSV (table)", ["pandas", "matplotlib"]),
    ("sample.xlsx", "image", 1, "XLSX (table)", ["openpyxl", "pandas", "matplotlib"]),
    ("sample.ts", "text", 1, "TypeScript (text)", []),
    ("sample.drawio", "image", 1, "DrawIO (XML → SVG)", ["resvg_py", "lxml"]),
    ("sample.srt", "text", 1, "SRT subtitle (text)", []),
    ("sample-model.glb", "image", 3, "GLB (blender)", ["blender"]),
    ("GothicRoseWindow.step", "image", 3, "STEP (trimesh)", ["trimesh"]),
    ("sample.geojson", "image", 1, "GeoJSON (geopandas)", ["geopandas"]),
    ("sample.ipynb", "text", 3, "Jupyter notebook", ["nbformat"]),
    ("charts.ipynb", "image", 3, "Jupyter notebook (charts)", ["nbformat"]),
    ("sample.tex", "image", 1, "LaTeX (pdflatex)", ["pdflatex"]),
    ("tex_project/main.tex", "image", 1, "LaTeX multi-file", ["pdflatex"]),
    ("broken.tex", "text", 1, "LaTeX fallback (text)", []),
]


@pytest.mark.parametrize("case", TESTS, ids=[t[3] for t in TESTS])
def test_renderer(case):
    filename, expected_type, min_items, _desc, requires = case
    missing = [r for r in requires if not _has_dep(r)]
    if missing:
        pytest.skip(f"missing deps: {', '.join(missing)}")
    filepath = os.path.join(ASSETS_DIR, filename)
    if not os.path.exists(filepath):
        pytest.skip("asset missing")

    content = handle({"file_path": filepath, "budget": "large"})
    errors = [c for c in content if c["type"] == "text" and c["text"].startswith("Error")]
    assert not errors, errors[0]["text"][:120] if errors else ""
    got = [c for c in content if c["type"] == expected_type]
    assert len(got) >= min_items, f"expected >={min_items} {expected_type}, got {len(got)}"

    # Output-quality checks (beyond "a block exists"): every image block must be real,
    # decodable pixel data; every text block must carry actual content, not whitespace.
    from PIL import Image

    for block in content:
        if block["type"] == "image":
            assert block.get("mimeType", "").startswith("image/"), f"bad mimeType: {block.get('mimeType')!r}"
            img = Image.open(io.BytesIO(base64.b64decode(block["data"])))
            assert img.size[0] > 0 and img.size[1] > 0, "rendered image must have non-zero dimensions"
        elif block["type"] == "text":
            assert block["text"].strip(), "text block must not be blank"
    if expected_type == "text":
        rendered = "".join(c["text"] for c in got)
        assert len(rendered.strip()) >= 20, f"text render suspiciously short: {rendered!r}"
