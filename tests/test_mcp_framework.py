"""Unit tests for the mcp_framework — the load-bearing transforms that are otherwise
only exercised indirectly by the protocol tests.

Covers: tool_schema normalization (the advertised inputSchema every tool depends on),
build_registry discovery hardening, and the SYSTEM_DEPS report/startup-warning logic.
"""

import json
import sys
from types import ModuleType
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
from pydantic import BaseModel
class A(BaseModel):
    x: int = 0
TOOL = {{"name": "{name}", "args": A}}
def handle(arguments):
    '''A documented tool.

    Args:
        x: A number.
    '''
    return [{{"type": "text", "text": "ok"}}]
"""
_HALF = """
from pydantic import BaseModel
class A(BaseModel):
    x: int = 0
TOOL = {"name": "half", "args": A}
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


@pytest.fixture
def cli_config(tmp_path, monkeypatch):

    config = tmp_path / "config"
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setitem(sys.modules, "test_cli_package", ModuleType("test_cli_package"))
    return config


@pytest.mark.parametrize("value", ["", " \t "])
@pytest.mark.parametrize("existing", [False, True])
def test_set_rejects_blank_values_without_writing(cli_config, monkeypatch, capsys, value, existing):
    initial = "# preserved comment\nBLENDER_PORT=9876\n"
    if existing:
        cli_config.write_text(initial, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "test-cli-package",
            "--set",
            "DASHSCOPE_API_KEY=test-secret",
            f"BLENDER_PORT={value}",
            "FREECAD_RPC_PORT=9875",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        fw.run_main("test_cli_package")

    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "non-empty value for BLENDER_PORT" in output.err
    assert "test-cli-package --unset BLENDER_PORT" in output.err
    assert "test-secret" not in output.err
    assert output.out == ""
    if existing:
        assert cli_config.read_text(encoding="utf-8") == initial
    else:
        assert not cli_config.exists()


def test_set_nonempty_values_and_unset_preserve_other_entries(cli_config, monkeypatch, capsys):
    cli_config.write_text("FREECAD_RPC_PORT=9875\n", encoding="utf-8")
    values = {
        "QWEN_MM_CACHE": "/path with spaces/cache",
        "DASHSCOPE_BASE_URL": "https://example.test/v1?tenant=a=b",
        "DASHSCOPE_API_KEY": "test-secret",
    }
    monkeypatch.setattr(sys, "argv", ["test-cli-package", "--set", *(f"{k}={v}" for k, v in values.items())])

    fw.run_main("test_cli_package")

    contents = cli_config.read_text(encoding="utf-8")
    assert "FREECAD_RPC_PORT=9875\n" in contents
    for key, value in values.items():
        assert f"{key}={value}\n" in contents
    output = capsys.readouterr()
    assert "wrote" in output.out
    assert "test-secret" not in output.out + output.err

    monkeypatch.setattr(sys, "argv", ["test-cli-package", "--unset", "DASHSCOPE_API_KEY"])
    fw.run_main("test_cli_package")

    contents = cli_config.read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=" not in contents
    assert "FREECAD_RPC_PORT=9875\n" in contents
    assert f"QWEN_MM_CACHE={values['QWEN_MM_CACHE']}\n" in contents
    assert f"DASHSCOPE_BASE_URL={values['DASHSCOPE_BASE_URL']}\n" in contents
