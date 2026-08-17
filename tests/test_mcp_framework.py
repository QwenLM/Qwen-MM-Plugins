"""Unit tests for the mcp_framework — the load-bearing transforms that are otherwise
only exercised indirectly by the protocol tests.

Covers: tool_schema normalization (the advertised inputSchema every tool depends on),
build_registry discovery hardening, and the SYSTEM_DEPS report/startup-warning logic.
"""

import json
import sys
from typing import Optional

import pytest
from pydantic import BaseModel, Field

import mcp_framework as fw

# ── tool_schema normalization (G2) ───────────────────────────────────


class _Sub(BaseModel):
    x: int = Field(description="a nested int")


class _Args(BaseModel):
    name: str = Field(description="the name")
    opt: Optional[str] = Field(default=None, description="an optional string")
    num: float | str = Field(default=1, description="a scalar union")
    sub: Optional[_Sub] = Field(default=None, description="a nested model")


def test_tool_schema_inlines_refs_and_drops_titles():
    schema = fw.tool_schema(_Args)
    blob = json.dumps(schema)
    assert "$defs" not in blob and "$ref" not in blob, "refs/defs must be inlined"
    assert '"title"' not in blob, "auto titles must be stripped"
    assert schema["type"] == "object"
    # per-property descriptions are preserved
    assert schema["properties"]["name"]["description"] == "the name"


def test_tool_schema_drops_null_default_on_optional():
    schema = fw.tool_schema(_Args)
    assert "default" not in schema["properties"]["opt"], "default: null must be dropped"
    # the Optional[str] collapses to a plain string type
    assert schema["properties"]["opt"]["type"] == "string"


def test_tool_schema_collapses_scalar_union():
    schema = fw.tool_schema(_Args)
    types = schema["properties"]["num"]["type"]
    assert isinstance(types, list) and set(types) == {"number", "string"}


def test_tool_schema_inlines_optional_complex():
    schema = fw.tool_schema(_Args)
    sub = schema["properties"]["sub"]
    assert sub["type"] == "object"
    assert "x" in sub["properties"], "nested model must be inlined, not left as a $ref"
    assert sub["description"] == "a nested model", "sibling description kept when inlining"


# ── build_registry discovery hardening (E1) ──────────────────────────

_GOOD = """
from pydantic import BaseModel, Field
class A(BaseModel):
    x: int = Field(default=0, description="x")
TOOL = {{"name": "{name}", "description": "d", "args": A}}
def handle(arguments):
    return [{{"type": "text", "text": "ok"}}]
"""
_HALF = """
from pydantic import BaseModel
class A(BaseModel):
    x: int = 0
TOOL = {"name": "half", "description": "d", "args": A}
"""  # exports TOOL but not handle
_HELPER = "VALUE = 1\n"  # neither TOOL nor handle — a legit helper module, must be skipped


def _make_pkg(tmp_path, modules: dict) -> str:
    import importlib

    pkg = "fwt_" + tmp_path.name.replace("-", "_")
    root = tmp_path / pkg
    tools = root / "tools"
    tools.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (tools / "__init__.py").write_text("")
    for name, src in modules.items():
        (tools / f"{name}.py").write_text(src)
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    return pkg


def test_build_registry_discovers_and_skips_helpers(tmp_path):
    pkg = _make_pkg(tmp_path, {"good": _GOOD.format(name="toolA"), "helper": _HELPER})
    specs, get_handler, _ = fw.build_registry(pkg, ["tools"])
    assert [s.name for s in specs] == ["toolA"]
    assert callable(get_handler("toolA"))
    assert get_handler("missing") is None


def test_build_registry_raises_on_half_defined_module(tmp_path):
    pkg = _make_pkg(tmp_path, {"half": _HALF})
    with pytest.raises(RuntimeError, match="but not handle"):
        fw.build_registry(pkg, ["tools"])


def test_build_registry_raises_on_duplicate_name(tmp_path):
    pkg = _make_pkg(tmp_path, {"a": _GOOD.format(name="dup"), "b": _GOOD.format(name="dup")})
    with pytest.raises(RuntimeError, match="duplicate tool name"):
        fw.build_registry(pkg, ["tools"])


# ── SYSTEM_DEPS report + startup warnings (G3) ───────────────────────


def test_system_report_marks_missing_and_collapses_dormant(monkeypatch):
    monkeypatch.setattr(fw, "_tool_present", lambda t: False)
    monkeypatch.setattr(fw, "_extra_installed", lambda probe: probe != "__absent__")
    deps = [
        {"label": "core-tool", "extra": None, "probe": None, "tools": ["x"], "hint": "hintA"},
        {"label": "viz-tool", "extra": "viz", "probe": "__present__", "tools": ["y"], "hint": "hintB"},
        {"label": "dormant-tool", "extra": "video-edit", "probe": "__absent__", "tools": ["z"], "hint": "hintC"},
    ]
    rep = fw.system_report(deps)
    assert "✗ core-tool [core]" in rep and "hintA" in rep
    assert "✗ viz-tool [viz]" in rep and "hintB" in rep
    assert "✗ dormant-tool" not in rep, "an uninstalled extra must not be itemized"
    assert "extras not installed" in rep and "video-edit" in rep


def test_system_startup_warnings_skips_report_only(monkeypatch):
    monkeypatch.setattr(fw, "_tool_present", lambda t: False)
    monkeypatch.setattr(fw, "_extra_installed", lambda probe: True)
    deps = [
        {"label": "warns", "extra": None, "probe": None, "tools": ["x"], "hint": "h1"},
        {"label": "silent", "extra": None, "probe": None, "tools": ["y"], "hint": "h2", "startup": False},
    ]
    warnings = fw.system_startup_warnings(deps)
    assert any("warns" in w for w in warnings)
    assert not any("silent" in w for w in warnings)


# ── _to_content_block: handler output → SDK blocks; a stray/malformed block must not crash ──
def test_to_content_block_text_and_image():
    import mcp.types as types

    t = fw._to_content_block({"type": "text", "text": "hi"})
    assert isinstance(t, types.TextContent) and t.text == "hi"
    im = fw._to_content_block({"type": "image", "data": "AAAA", "mimeType": "image/png"})
    assert isinstance(im, types.ImageContent) and im.data == "AAAA"
    assert im.model_dump(by_alias=True)["_meta"] == {"codex/imageDetail": "original"}


def test_to_content_block_malformed_image_falls_back_to_text():
    import mcp.types as types

    # image block missing "data" must NOT raise KeyError — it falls back to JSON text
    blk = fw._to_content_block({"type": "image", "mimeType": "image/png"})
    assert isinstance(blk, types.TextContent)
    # an unknown block type also falls back to text rather than crashing the call
    blk2 = fw._to_content_block({"type": "weird", "foo": 1})
    assert isinstance(blk2, types.TextContent)
