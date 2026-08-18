"""Structure tests for the edu-agent capability (P2.2) — skill-only, no MCP server.

Validates the SKILL.md frontmatter (name follows the qwen-mm-plugins-<folder> convention
from CLAUDE.md) and the three harness plugin manifests. Deliberately does NOT pin the
body's section layout or first heading — the skill prose is actively edited (e.g. a
Troubleshooting index may be prepended) and that must not break these checks.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from conftest import REPO_ROOT

CAP_DIR = os.path.join(REPO_ROOT, "src", "capabilities", "edu-agent")
SKILL_MD = os.path.join(CAP_DIR, "skill", "SKILL.md")
EXPECTED_NAME = "qwen-mm-plugins-edu-agent"
SKILL_ROOT = Path(CAP_DIR) / "skill"
POSTCHECK = SKILL_ROOT / "scripts/postcheck.py"
FILE_URI_SCRIPTS = [
    "check_scene_fit.py",
    "check_render_overlap.py",
    "check_smooth_curve_render.py",
    "check_circuit_closed.py",
    "check_curves_rendered.py",
]

MANIFESTS = [".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".qoder-plugin/plugin.json"]


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split '---\\n<yaml>\\n---\\n<body>' without requiring a YAML parser."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.DOTALL)
    assert m, "SKILL.md must start with a --- frontmatter block"
    return m.group(1), m.group(2)


@pytest.fixture(scope="module")
def skill_parts() -> tuple[str, str]:
    assert os.path.isfile(SKILL_MD), f"missing {SKILL_MD}"
    with open(SKILL_MD, encoding="utf-8") as f:
        return _split_frontmatter(f.read())


def test_skill_name_matches_naming_convention(skill_parts):
    frontmatter, _ = skill_parts
    m = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    assert m, "frontmatter must declare a name:"
    assert m.group(1) == EXPECTED_NAME, "SKILL.md name must equal the install/plugin name (CLAUDE.md convention)"


def test_skill_description_present(skill_parts):
    frontmatter, _ = skill_parts
    assert re.search(r"^description:", frontmatter, re.MULTILINE), "frontmatter must declare a description:"
    # description carries real trigger guidance, not a stub
    assert len(frontmatter) > 200


def test_skill_body_nonempty(skill_parts):
    _, body = skill_parts
    assert len(body.strip()) > 500, "SKILL.md body must hold real instructions"
    # at least one markdown heading somewhere — but no assumption about WHICH comes first
    assert re.search(r"^#{1,3} ", body, re.MULTILINE)


@pytest.mark.parametrize("rel", MANIFESTS)
def test_manifest_valid_json_with_key_fields(rel):
    path = os.path.join(CAP_DIR, rel)
    assert os.path.isfile(path), f"missing manifest {rel}"
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)  # raises on invalid JSON
    assert manifest["name"] == EXPECTED_NAME
    assert re.match(r"^\d+\.\d+", manifest["version"]), "version must look like a semver"
    assert manifest.get("description", "").strip()


@pytest.mark.parametrize("rel", MANIFESTS)
def test_manifest_skills_path_resolves(rel):
    with open(os.path.join(CAP_DIR, rel), encoding="utf-8") as f:
        manifest = json.load(f)
    skills = manifest.get("skills")
    assert skills, f"{rel} must reference the skill dir"
    # claude uses a list, codex/qoder a single string — accept both shapes
    entries = skills if isinstance(skills, list) else [skills]
    for entry in entries:
        target = os.path.normpath(os.path.join(CAP_DIR, entry))
        assert os.path.isdir(target), f"{rel} skills path {entry!r} must exist"
        assert os.path.isfile(os.path.join(target, "SKILL.md"))


@pytest.mark.parametrize("script_name", FILE_URI_SCRIPTS)
def test_browser_checks_use_node_file_url_conversion(script_name):
    source = (SKILL_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
    assert "pathToFileURL" in source
    assert "page.goto('file://'" not in source


@pytest.mark.parametrize("script_name", FILE_URI_SCRIPTS)
def test_browser_check_node_driver_syntax(script_name, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")

    scripts_dir = str(SKILL_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    script_path = SKILL_ROOT / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(f"edu_uri_test_{script_path.stem}", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    driver_path = tmp_path / f"{script_path.stem}.js"
    driver_path.write_text(module.NODE_DRIVER, encoding="utf-8")
    result = subprocess.run([node, "--check", str(driver_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_postcheck_fails_when_rendered_video_is_missing(tmp_path):
    dist = tmp_path / "dist"
    compositions = dist / "compositions"
    compositions.mkdir(parents=True)
    (dist / "index.html").write_text('<div data-composition-src="compositions/scene.html"></div>', encoding="utf-8")
    (compositions / "scene.html").write_text('<div data-composition-id="scene"></div>', encoding="utf-8")
    proc = subprocess.run([sys.executable, str(POSTCHECK), str(dist)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "rendered MP4 is missing" in proc.stdout
    assert "safe to deliver" not in proc.stdout


def test_postcheck_does_not_treat_skipped_checks_as_passed(monkeypatch, tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("edu_postcheck_test", POSTCHECK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "output.mp4").write_bytes(b"rendered")
    monkeypatch.setattr(module.sys, "argv", [str(POSTCHECK), str(dist)])
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0, stdout="NOTE: check skipped — no Chrome", stderr=""
        ),
    )

    assert module.main() == 1
    output = capsys.readouterr().out
    assert "POSTCHECK INCOMPLETE" in output
    assert "safe to deliver" not in output
