"""Configuration regressions for the standalone video-memory builder and its MCP toolkit loader."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import env as env_config

_BUILD_DIR = Path(__file__).resolve().parents[1] / "src/capabilities/video-memory/skill/script/build_memory"


@pytest.mark.parametrize("source", ["environment", "config"])
@pytest.mark.parametrize(
    "value,expected,warns",
    [
        (None, 7200, False),
        ("", 7200, False),
        ("   ", 7200, False),
        ("invalid", 7200, True),
        ("1.5", 7200, True),
        ("15 MiB", 7200, True),
        ("3600", 3600, False),
        (" 9000 ", 9000, False),
    ],
)
def test_builder_import_tolerates_url_expiry(tmp_path, source, value, expected, warns):
    config = tmp_path / "config"
    config.write_text(f"OSS_URL_EXPIRY={value}\n" if source == "config" and value is not None else "")
    env = {k: v for k, v in os.environ.items() if k not in {"OSS_URL_EXPIRY", "PYTHONPATH"}}
    env["QWEN_MM_CONFIG"] = str(config)
    if source == "environment" and value is not None:
        env["OSS_URL_EXPIRY"] = value

    result = subprocess.run(
        [sys.executable, "-c", "import build_graph; print(build_graph.URL_EXPIRY)"],
        cwd=_BUILD_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert result.stdout.strip() == str(expected)
    assert ("invalid OSS_URL_EXPIRY=" in result.stderr) is warns


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("90", 90.0),
        ("  90.5  ", 90.5),
        ("1:30", None),
        ("90s", None),
    ],
)
def test_toolkit_load_survives_the_cutoff_knob(tmp_path, monkeypatch, caplog, value, expected):
    """A blank or human-typed CUTOFF_SEC must degrade to "no cutoff", not fail every tool.

    The loader resolved the cutoff with a bare ``float(...)``, so one mistyped value raised on
    the first tool call while the startup pre-load swallowed it as a warning.
    """
    from qwen_mm_plugins_video_memory import loader

    config = tmp_path / "config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setattr(env_config, "_config_cache", None)
    graph = tmp_path / "graph_memory.json"
    graph.write_text(json.dumps({"macro_events": [], "nodes": [], "edges": []}), encoding="utf-8")
    monkeypatch.setenv("GRAPH_MEMORY_PATH", str(graph))
    monkeypatch.delenv("EMBEDDINGS_PATH", raising=False)
    if value is None:
        monkeypatch.delenv("CUTOFF_SEC", raising=False)
    else:
        monkeypatch.setenv("CUTOFF_SEC", value)

    assert loader.get_toolkit()._cutoff_sec == expected
    assert (f"invalid CUTOFF_SEC={value!r}" in caplog.text) is (value in ("1:30", "90s"))
