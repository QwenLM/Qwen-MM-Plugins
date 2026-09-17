"""Verify that local scripts can reuse and check the MCP Python environment."""

import json
import subprocess
import sys
import venv
from pathlib import Path

import pytest

import qwen_mm_plugins_omni_chatcut as chatcut
from qwen_mm_plugins_omni_chatcut.music_to_mv import pipeline

RUNNER = (
    Path(__file__).resolve().parents[2]
    / "src/capabilities/omni-chatcut/skill/music-to-mv/workflows/video-generation/scripts/run_mv_pipeline.py"
)


def test_runtime_tool_and_matching_launcher():
    handler = chatcut.get_handler("get_music2mv_runtime")
    assert callable(handler)
    runtime = json.loads(handler({})[0]["text"])
    assert runtime == {
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
        "python_version": sys.version.split()[0],
    }
    result = subprocess.run(
        [runtime["python_executable"], str(RUNNER), "--expected-python-prefix", runtime["python_prefix"], "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "base-assets" in result.stdout


@pytest.mark.parametrize("equals_form", [False, True])
def test_wrong_prefix_is_rejected_before_dependency_imports(tmp_path, equals_form):
    expected = str(tmp_path / "a different environment")
    flags = [f"--expected-python-prefix={expected}"] if equals_form else ["--expected-python-prefix", expected]
    # -S hides site-packages. The prefix error must still precede missing package errors.
    result = subprocess.run([sys.executable, "-S", str(RUNNER), *flags, "--help"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "MCP Python environment mismatch" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_another_venv_with_the_same_base_python_is_rejected(tmp_path):
    other = tmp_path / "other environment"
    venv.EnvBuilder(with_pip=False).create(other)
    executable = other / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    info = subprocess.run(
        [str(executable), "-c", "import sys,json; print(json.dumps([sys.base_prefix,sys.prefix]))"],
        capture_output=True,
        text=True,
        check=True,
    )
    base_prefix, other_prefix = json.loads(info.stdout)
    assert Path(base_prefix).resolve() == Path(sys.base_prefix).resolve()
    assert Path(other_prefix).resolve() != Path(sys.prefix).resolve()
    result = subprocess.run(
        [str(executable), str(RUNNER), "--expected-python-prefix", sys.prefix, "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "MCP Python environment mismatch" in result.stderr


def test_validator_subprocess_keeps_current_interpreter(tmp_path, monkeypatch):
    instance = object.__new__(pipeline.Pipeline)
    instance.output_dir = tmp_path
    instance.storyboard_path = tmp_path / "storyboard.json"
    instance.validate_video_provider_storyboard = lambda: None
    calls = []
    monkeypatch.setattr(pipeline.subprocess, "run", lambda command, **kwargs: calls.append(command))
    instance.validate()
    assert calls[0][0] == sys.executable
    assert calls[0][1].endswith("validate_execution_storyboard.py")
