"""Verify generated configuration readers stay synced and work outside the installed package."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.sync_env_readers import mirror_paths, sync

_ROOT = Path(__file__).resolve().parents[1]
_MIRRORS = mirror_paths(_ROOT)


def test_standalone_readers_are_current():
    assert sync(_ROOT) == 0


def test_sync_repairs_only_the_mirrored_region(tmp_path):
    source = tmp_path / "src/shared/env.py"
    mirror = tmp_path / "src/capabilities/example/skill/env_config.py"
    source.parent.mkdir(parents=True)
    mirror.parent.mkdir(parents=True)
    source.write_text((_ROOT / "src/shared/env.py").read_text(encoding="utf-8"), encoding="utf-8")
    original = _MIRRORS[0].read_text(encoding="utf-8") + "\nPLUGIN_SETTING = 'keep'\n"
    mirror.write_text(original.replace("def get_env(", "def stale_get_env("), encoding="utf-8")

    assert sync(tmp_path) == 1
    assert sync(tmp_path, write=True) == 0
    assert mirror.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("path", _MIRRORS, ids=lambda p: p.relative_to(_ROOT).parts[2])
def test_standalone_reader_imports_and_reads_config(path, tmp_path):
    config = tmp_path / "config"
    config.write_text("QWEN_MM_FFMPEG_TIMEOUT=45\n", encoding="utf-8")
    env = {**os.environ, "QWEN_MM_CONFIG": str(config)}
    env.pop("QWEN_MM_FFMPEG_TIMEOUT", None)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import runpy, sys; reader = runpy.run_path(sys.argv[1]); "
            "assert reader['get_int_env']('QWEN_MM_FFMPEG_TIMEOUT') == 45; "
            "assert 'shared.env' not in sys.modules",
            str(path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout == result.stderr == ""
