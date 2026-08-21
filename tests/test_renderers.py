"""Renderer matrix: assert visualize.handle() renders each supported format.

Parametrized pytest; a case whose optional dependency or asset is missing is skipped.
"""

import base64
import io
import os
import shutil
import sys
import types
from pathlib import Path, PureWindowsPath

import pytest

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from qwen_mm_plugins_core.visualizers.visualize import handle  # noqa: E402  (import after sys.path setup)


def test_windows_3d_uses_generic_isolated_worker(monkeypatch):
    from qwen_mm_plugins_core.renderers import model3d
    from shared import isolated_worker

    expected = [{"type": "image", "data": "encoded", "mimeType": "image/png"}]
    captured = {}

    def fake_run_isolated(module, function, arguments, **options):
        captured.update(module=module, function=function, arguments=arguments, options=options)
        return expected

    monkeypatch.setattr(model3d, "_WINDOWS", True)
    monkeypatch.setattr(isolated_worker, "run_isolated", fake_run_isolated)
    monkeypatch.setattr(
        model3d,
        "_render_backends",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Windows parent must not load render backends")),
    )

    assert model3d.render("sample.stl", max_pages=1, budget="small") == expected
    assert captured["module"] == model3d.__name__
    assert captured["function"] == "render_isolated"
    assert captured["arguments"] == {
        "path": "sample.stl",
        "options": {"max_pages": 1, "budget": "small"},
    }
    assert captured["options"]["env_overrides"] == {"MPLBACKEND": "Agg"}


def test_isolated_3d_worker_preserves_backend_fallback_order(monkeypatch):
    from qwen_mm_plugins_core.renderers import model3d

    calls = []
    meshes = [object()]
    expected = [{"type": "image", "data": "matplotlib", "mimeType": "image/png"}]
    monkeypatch.setitem(sys.modules, "trimesh", types.ModuleType("trimesh"))
    monkeypatch.setattr(
        model3d,
        "render_blender",
        lambda *args, **kwargs: (calls.append("blender"), (_ for _ in ()).throw(RuntimeError("no blender")))[1],
    )
    monkeypatch.setattr(model3d, "_load_scene", lambda path: (calls.append("load"), (meshes, 3, 1))[1])
    monkeypatch.setattr(model3d, "_has_real_materials", lambda value: False)
    monkeypatch.setattr(
        model3d,
        "_render_pyrender",
        lambda *args: (calls.append("pyrender"), (_ for _ in ()).throw(RuntimeError("no GL")))[1],
    )
    monkeypatch.setattr(
        model3d,
        "_render_matplotlib",
        lambda *args: (calls.append("matplotlib"), [object()])[1],
    )
    monkeypatch.setattr(model3d, "_images_to_content", lambda *args: expected)

    result = model3d.render_isolated({"path": "sample.stl", "options": {"max_pages": 1}})
    assert result == expected
    assert calls == ["blender", "load", "pyrender", "matplotlib"]


def test_web_renderer_uses_generic_isolated_worker(monkeypatch):
    from qwen_mm_plugins_core.renderers import web
    from shared import isolated_worker

    expected = [{"type": "image", "data": "encoded", "mimeType": "image/png"}]
    captured = {}

    def fake_run_isolated(module, function, arguments, **options):
        captured.update(module=module, function=function, arguments=arguments, options=options)
        return expected

    monkeypatch.setattr(isolated_worker, "run_isolated", fake_run_isolated)
    monkeypatch.setattr(
        web,
        "_render_in_process",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Playwright must not run in the MCP parent")),
    )

    assert web.render("sample.html", max_pages=2, budget="small") == expected
    assert captured == {
        "module": web.__name__,
        "function": "render_isolated",
        "arguments": {"path": "sample.html", "options": {"max_pages": 2, "budget": "small"}},
        "options": {"timeout": web._ISOLATED_TIMEOUT},
    }


def test_web_source_url_uses_platform_file_uri(tmp_path):
    from qwen_mm_plugins_core.renderers.web import _source_url_and_label

    source = tmp_path / "页面 # 100%.html"
    url, label = _source_url_and_label(str(source))

    assert url == source.resolve().as_uri()
    assert label == source.name
    assert " " not in url
    assert "#" not in url
    assert "%23" in url
    assert "%25" in url


def test_windows_file_uri_encodes_special_characters():
    uri = PureWindowsPath(r"C:\Users\Qwen User\页面 # 100%.html").as_uri()

    assert uri.startswith("file:///C:/Users/Qwen%20User/")
    assert "\\" not in uri
    assert " " not in uri
    assert "#" not in uri
    assert "%23" in uri
    assert "%25" in uri


def test_web_source_url_preserves_http_urls():
    from qwen_mm_plugins_core.renderers.web import _source_url_and_label

    url = "https://example.com/page?q=hello%20world#section"
    assert _source_url_and_label(url) == (url, url)


def test_office_profile_uses_platform_file_uri(tmp_path, monkeypatch):
    from qwen_mm_plugins_core.renderers import office
    from shared.paths import path_to_file_uri

    source = tmp_path / "文档 # 100%.docx"
    source.write_bytes(b"docx")
    destination = tmp_path / "output.pdf"
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / "converted.pdf").write_bytes(b"pdf")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(office.subprocess, "run", fake_run)
    office._pdf_producer(str(source), "soffice")(str(destination))

    command = captured["command"]
    output_dir = Path(command[command.index("--outdir") + 1])
    profile = next(argument for argument in command if argument.startswith("-env:UserInstallation="))
    assert profile == f"-env:UserInstallation={path_to_file_uri(output_dir / 'lo_profile')}"
    assert profile.startswith("-env:UserInstallation=file:///")
    assert "\\" not in profile
    assert destination.read_bytes() == b"pdf"
    assert captured["kwargs"] == {"capture_output": True, "text": True, "timeout": 120}


def test_office_failure_reports_return_code_when_libreoffice_is_silent(tmp_path, monkeypatch):
    from qwen_mm_plugins_core.renderers import office

    monkeypatch.setattr(
        office.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    producer = office._pdf_producer(str(tmp_path / "sample.docx"), "soffice")
    with pytest.raises(RuntimeError, match="exit code 1 with no output"):
        producer(str(tmp_path / "output.pdf"))


def test_web_worker_closes_browser_when_navigation_fails(monkeypatch):
    from qwen_mm_plugins_core.renderers import web

    class FakePage:
        def goto(self, *args, **kwargs):
            raise RuntimeError("navigation failed")

    class FakeBrowser:
        closed = False

        def new_page(self, **kwargs):
            return FakePage()

        def close(self):
            self.closed = True

    browser = FakeBrowser()

    class FakePlaywright:
        chromium = types.SimpleNamespace(launch=lambda **kwargs: browser)

    class FakeContext:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *args):
            return False

    playwright = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = FakeContext
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    with pytest.raises(RuntimeError, match="navigation failed"):
        web._render_in_process("sample.html", {})
    assert browser.closed


def test_web_isolated_entry_validates_arguments():
    from qwen_mm_plugins_core.renderers.web import render_isolated

    with pytest.raises(ValueError, match="non-empty path"):
        render_isolated({"path": "", "options": {}})
    with pytest.raises(ValueError, match="options must be an object"):
        render_isolated({"path": "sample.html", "options": []})


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
        "playwright": lambda: __import__("playwright"),
        "libreoffice": lambda: shutil.which("libreoffice") or shutil.which("soffice"),
        "pdflatex": lambda: shutil.which("pdflatex"),
        "blender": lambda: shutil.which("blender"),
    }
    try:
        return bool(checks[key]())
    except Exception:
        return False


def _error_text(content) -> str | None:
    return next(
        (
            block["text"]
            for block in content
            if block.get("type") == "text" and block.get("text", "").startswith("Error")
        ),
        None,
    )


def _assert_decodable_image(content) -> None:
    from PIL import Image

    images = [block for block in content if block.get("type") == "image"]
    assert images, "renderer produced no image blocks"
    for block in images:
        assert block.get("mimeType", "").startswith("image/")
        image = Image.open(io.BytesIO(base64.b64decode(block["data"])))
        assert image.size[0] > 0 and image.size[1] > 0


def test_office_real_render_with_special_character_path(tmp_path):
    missing = [dependency for dependency in ("libreoffice", "pypdfium2") if not _has_dep(dependency)]
    if missing:
        pytest.skip(f"missing deps: {', '.join(missing)}")

    source = Path(ASSETS_DIR) / "sample.docx"
    special_path = tmp_path / "文档 # 100%.docx"
    shutil.copy2(source, special_path)

    content = handle({"file_path": str(special_path), "budget": "small", "max_pages": 1})
    error = _error_text(content)
    assert error is None, f"DOCX special-character path render errored: {error}"
    _assert_decodable_image(content)


def test_web_real_render_with_special_character_path(tmp_path):
    if not _has_dep("playwright"):
        pytest.skip("missing dep: playwright")

    source = Path(ASSETS_DIR) / "sample.html"
    special_path = tmp_path / "页面 # 100%.html"
    shutil.copy2(source, special_path)

    content = handle({"file_path": str(special_path), "budget": "small", "max_pages": 1})
    error = _error_text(content)
    environment_gaps = (
        "Executable doesn't exist",
        "playwright install",
        "Host system is missing dependencies",
        "cannot open shared object",
    )
    if error and any(message in error for message in environment_gaps):
        pytest.skip(f"Playwright browser unavailable: {error[:120]}")
    assert error is None, f"HTML special-character path render errored: {error}"
    _assert_decodable_image(content)


def test_3d_real_render_smoke():
    missing = [dependency for dependency in ("trimesh", "matplotlib") if not _has_dep(dependency)]
    if missing:
        pytest.skip(f"missing deps: {', '.join(missing)}")

    content = handle(
        {
            "file_path": str(Path(ASSETS_DIR) / "sample.stl"),
            "budget": "small",
            "max_pages": 1,
        }
    )
    error = _error_text(content)
    assert error is None, f"3D render errored: {error}"
    _assert_decodable_image(content)


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
    ("sample.tex", "image", 1, "LaTeX (pdflatex)", ["pdflatex", "pypdfium2"]),
    ("tex_project/main.tex", "image", 1, "LaTeX multi-file", ["pdflatex", "pypdfium2"]),
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
