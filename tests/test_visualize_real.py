"""Real-world render tests for the visualize() pipeline.

Renders committed real-world assets (PDF, LaTeX, DOCX, notebook, CSV, markdown,
offline HTML, GLB) through visualize.handle() and asserts each yields non-error
content. Assets live in tests/assets/real/ (~13 MB). A case is skipped when its
asset is absent or the renderer's optional dependency isn't available, so the
suite stays green on a minimal install (and errors that are clearly an
environment gap — e.g. no chromium browser — skip rather than fail).

Manual gallery (renders everything to a browsable HTML page for eyeballing):
    python3 tests/test_visualize_real.py            # -> tests/output/real/gallery.html
    python3 tests/test_visualize_real.py pdf docx   # only a subset
"""

import html as html_mod
import importlib.util
import os
import shutil
import sys

import pytest
from conftest import REPO_ROOT

from qwen_mm_plugins_core.visualizers import visualize

ASSETS_DIR = os.path.join(REPO_ROOT, "tests", "assets", "real")
OUTPUT_DIR = os.path.join(REPO_ROOT, "tests", "output", "real")


def _mod(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _tool(*names: str) -> bool:
    return any(shutil.which(n) for n in names)


# id, asset path (relative to ASSETS_DIR), description, visualize() extra args, availability predicate
CASES = [
    ("pdf", "qwen3vl.pdf", "Qwen3-VL tech report (PDF)", {"max_pages": 3}, lambda: _mod("pypdfium2")),
    (
        "latex",
        "qwen3vl-tex/colm2024_conference.tex",
        "Qwen3-VL paper (LaTeX)",
        {"max_pages": 2},
        lambda: _tool("pdflatex", "xelatex") and _mod("pypdfium2"),
    ),
    (
        "docx",
        "calibre-test.docx",
        "Calibre DOCX demo",
        {"max_pages": 3},
        lambda: _tool("libreoffice", "soffice") and _mod("pypdfium2"),
    ),
    ("notebook", "hf-quicktour.ipynb", "HuggingFace quicktour", {}, lambda: _mod("nbformat")),
    ("csv", "titanic.csv", "Titanic dataset", {}, lambda: _mod("pandas") and _mod("matplotlib")),
    ("markdown", "llama-cpp-readme.md", "llama.cpp README", {}, lambda: True),
    (
        "html",
        "python-docs/3/tutorial/index.html",
        "Python tutorial (offline HTML)",
        {"max_pages": 3},
        lambda: _mod("playwright"),
    ),
    ("3d", "grand_piano.glb", "Grand piano (GLB)", {"max_pages": 3}, lambda: _mod("trimesh")),
]

# Error substrings that indicate an environment gap (missing browser/tool/module),
# not a render bug — a case hitting these is skipped rather than failed.
_ENV_GAP = (
    "install",
    "Executable doesn't exist",
    "not found",
    "requires",
    "No module",
    "not available",
    "shared libraries",  # browser downloaded but system libs missing (e.g. libatk)
    "cannot open shared object",
    "has been closed",  # browser failed to launch
)


def _error_text(content) -> str | None:
    for b in content:
        if b.get("type") == "text" and b.get("text", "").startswith("Error"):
            return b["text"]
    return None


@pytest.mark.parametrize("case_id,rel_path,_desc,extra,available", CASES, ids=[c[0] for c in CASES])
def test_visualize_real_asset(case_id, rel_path, _desc, extra, available):
    path = os.path.join(ASSETS_DIR, rel_path)
    if not os.path.exists(path):
        pytest.skip(f"asset missing: {rel_path} (restore tests/assets/real/)")
    if not available():
        pytest.skip(f"renderer dependency for '{case_id}' not available")
    content = visualize.handle({"file_path": path, "budget": "large", **extra})
    err = _error_text(content)
    if err and any(s in err for s in _ENV_GAP):
        pytest.skip(f"{case_id}: environment gap — {err[:80]}")
    assert err is None, f"{case_id} render errored: {err}"
    assert content, f"{case_id} produced no content blocks"
    assert any(b.get("type") in ("image", "text") for b in content)


def test_visualize_real_office_path_with_special_characters(tmp_path):
    source = os.path.join(ASSETS_DIR, "calibre-test.docx")
    if not os.path.exists(source):
        pytest.skip("DOCX asset missing")
    if not (_tool("libreoffice", "soffice") and _mod("pypdfium2")):
        pytest.skip("Office renderer dependencies unavailable")

    special_path = tmp_path / "文档 # 100%.docx"
    shutil.copy2(source, special_path)
    content = visualize.handle({"file_path": str(special_path), "budget": "small", "max_pages": 1})
    err = _error_text(content)
    if err and any(message in err for message in _ENV_GAP):
        pytest.skip(f"docx: environment gap — {err[:80]}")
    assert err is None, f"DOCX special-character path render errored: {err}"
    assert any(block.get("type") == "image" for block in content)


# ── manual gallery (not collected by pytest) ─────────────────────────

HTML_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Qwen-MM-Plugins — Real-World Render Gallery</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f5; color: #222; line-height: 1.5; padding: 24px; }
  h1 { text-align: center; margin-bottom: 8px; }
  .subtitle { text-align: center; color: #888; margin-bottom: 32px; font-size: 14px; }
  .toc { max-width: 720px; margin: 0 auto 32px; }
  .toc a { display: inline-block; margin: 4px 8px; padding: 4px 12px;
           background: #e0e0e0; border-radius: 16px; text-decoration: none; color: #333;
           font-size: 13px; }
  .toc a:hover { background: #4a90d9; color: #fff; }
  .asset { max-width: 960px; margin: 0 auto 48px; background: #fff;
           border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08);
           overflow: hidden; }
  .asset-header { background: #4a90d9; color: #fff; padding: 16px 24px; }
  .asset-header h2 { font-size: 18px; margin-bottom: 4px; }
  .asset-header .meta { font-size: 13px; opacity: 0.85; }
  .block { padding: 16px 24px; border-bottom: 1px solid #eee; }
  .block:last-child { border-bottom: none; }
  .block img { max-width: 100%; height: auto; border-radius: 4px; }
  .block pre { background: #1e1e2e; color: #cdd6f4; padding: 16px;
               border-radius: 8px; overflow-x: auto; font-size: 13px;
               line-height: 1.4; white-space: pre-wrap; word-wrap: break-word;
               max-height: 400px; overflow-y: auto; }
  .block .label { color: #4a90d9; font-weight: 600; font-size: 13px;
                  margin-bottom: 6px; }
  .status { text-align: center; padding: 24px; color: #999; }
</style>
</head>
<body>
<h1>Qwen-MM-Plugins Render Gallery</h1>
<div class="subtitle">Real-world assets rendered through the unified visualize() pipeline</div>
"""

HTML_TAIL = "</body>\n</html>\n"


def _blocks_to_html(blocks) -> str:
    parts = []
    for block in blocks:
        if block["type"] == "image" and "data" in block:
            mime = block.get("mimeType", "image/png")
            parts.append(f'<div class="block"><img src="data:{mime};base64,{block["data"]}" alt="rendered page"></div>')
        elif block["type"] == "text":
            text = block["text"]
            if len(text) < 200 and text.startswith("[") and "\n" not in text:
                parts.append(f'<div class="block"><div class="label">{html_mod.escape(text)}</div></div>')
            else:
                parts.append(f'<div class="block"><pre>{html_mod.escape(text)}</pre></div>')
    return "\n".join(parts)


def _render_gallery(selected=None) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cases = [c for c in CASES if not selected or c[0] in selected]
    toc, sections = [], []
    print(f"\nRendering {len(cases)} asset(s) → {OUTPUT_DIR}/\n")
    for case_id, rel_path, desc, extra, available in cases:
        path = os.path.join(ASSETS_DIR, rel_path)
        toc.append(f'<a href="#{case_id}">{case_id}</a>')
        if not os.path.exists(path):
            status, blocks = "SKIP (asset missing)", None
        elif not available():
            status, blocks = "SKIP (dependency missing)", None
        else:
            blocks = visualize.handle({"file_path": path, "budget": "large", **extra})
            err = _error_text(blocks)
            status, blocks = (f"ERROR: {err[:60]}", None) if err else (f"OK ({len(blocks)} blocks)", blocks)
        print(f"  {case_id:10} {status}")
        if blocks:
            sections.append(
                f'<div class="asset" id="{case_id}">\n'
                f'  <div class="asset-header"><h2>{html_mod.escape(desc)}</h2>'
                f'<div class="meta">{html_mod.escape(status)}</div></div>\n'
                f"{_blocks_to_html(blocks)}\n</div>"
            )
        else:
            sections.append(
                f'<div class="asset" id="{case_id}">\n'
                f'  <div class="asset-header"><h2>{html_mod.escape(desc)}</h2></div>\n'
                f'  <div class="status">{html_mod.escape(status)}</div>\n</div>'
            )
    out = os.path.join(OUTPUT_DIR, "gallery.html")
    with open(out, "w") as f:
        f.write(HTML_HEAD)
        f.write(f'<div class="toc">{" ".join(toc)}</div>\n')
        f.write("\n".join(sections))
        f.write(HTML_TAIL)
    print(f"\nGallery: {out}")


if __name__ == "__main__":
    sys.exit(_render_gallery({a for a in sys.argv[1:] if not a.startswith("-")} or None))
