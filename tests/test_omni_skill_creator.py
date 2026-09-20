"""Structure tests for the omni-skill-creator capability — Skill + MCP server.

Modelled on tests/test_edu_agent.py. Every convention asserted here was actually broken at
some point: the skill frontmatter carried a bare `omni-skill-creator`, the Claude manifest keyed
its MCP server as `main`, and the server package re-exported mcp_framework's release-train
`__version__` instead of declaring its own. check_manifests.py catches the last two only once the
capability appears in plugin-versions.json, and never looks at SKILL.md at all — so these live
here, where they hold whether or not the capability is registered for release.

Deliberately does NOT pin the skill body's section layout: the prose is actively edited and that
must not break these checks.
"""

import json
import os
import re

import pytest
from conftest import REPO_ROOT, mcp_call

CAP = "omni-skill-creator"
EXPECTED_NAME = f"qwen-mm-plugins-{CAP}"
IMPORT_NAME = "qwen_mm_plugins_omni_skill_creator"
PUBLIC_DESCRIPTION = "Turns a demonstration video into a reusable Agent Skill. Needs a DashScope key and ffmpeg."

EXPECTED_TOOLS = {
    "create_storyboard",
    "crop_frame",
    "cutout_frame",
    "dedup_audio",
    "dedup_frames",
    "detect_scenes",
    "extract_audio_clip",
    "extract_clip",
    "get_video_metadata",
    "grounding",
    "image_annotate",
    "ocr_frames",
    "read_native_av",
    "validate_skill",
}

CAP_DIR = os.path.join(REPO_ROOT, "src", "capabilities", CAP)
SKILL_MD = os.path.join(CAP_DIR, "skill", "SKILL.md")
MANIFESTS = [".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".qoder-plugin/plugin.json"]


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split '---\\n<yaml>\\n---\\n<body>' without requiring a YAML parser."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.DOTALL)
    assert m, "SKILL.md must start with a --- frontmatter block"
    return m.group(1), m.group(2)


def _load(rel: str) -> dict:
    with open(os.path.join(CAP_DIR, rel), encoding="utf-8") as f:
        return json.load(f)


def test_server_lists_all_tools_and_validates_a_skill_over_stdio(tmp_path):
    """The main framework's docstring migration must preserve discovery and offline calls."""
    skill_dir = tmp_path / "incomplete_skill"
    skill_dir.mkdir()

    async def exercise(session):
        listed = await session.list_tools()
        assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS
        av = next(tool for tool in listed.tools if tool.name == "read_native_av")
        assert av.description
        assert av.inputSchema["properties"]["fps"]["type"] == "number"
        assert "fps" not in av.inputSchema.get("required", [])
        assert "start_sec" not in av.inputSchema.get("required", [])

        result = await session.call_tool("validate_skill", {"skill_dir": str(skill_dir)})
        assert not result.isError
        report = json.loads(result.content[0].text)
        assert report["status"] == "fail"
        assert any("SKILL.md" in error for error in report["errors"])

    mcp_call(os.path.join(CAP_DIR, IMPORT_NAME), exercise)


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
    assert re.search(r"^#{1,3} ", body, re.MULTILINE)


@pytest.mark.parametrize("rel", MANIFESTS)
def test_manifest_valid_json_with_key_fields(rel):
    path = os.path.join(CAP_DIR, rel)
    assert os.path.isfile(path), f"missing manifest {rel}"
    manifest = _load(rel)  # raises on invalid JSON
    assert manifest["name"] == EXPECTED_NAME
    assert re.match(r"^\d+\.\d+", manifest["version"]), "version must look like a semver"
    assert manifest.get("description") == PUBLIC_DESCRIPTION


@pytest.mark.parametrize("rel", MANIFESTS)
def test_manifest_skills_path_resolves(rel):
    skills = _load(rel).get("skills")
    assert skills, f"{rel} must reference the skill dir"
    # claude uses a list, codex/qoder a single string — accept both shapes
    entries = skills if isinstance(skills, list) else [skills]
    for entry in entries:
        target = os.path.normpath(os.path.join(CAP_DIR, entry))
        assert os.path.isdir(target), f"{rel} skills path {entry!r} must exist"
        assert os.path.isfile(os.path.join(target, "SKILL.md"))


def test_mcp_server_keys_use_the_capability_name():
    """Qwen Code namespaces MCP servers globally, so a generic key like 'main' can collide."""
    for source, servers in (
        (".claude-plugin/plugin.json", _load(".claude-plugin/plugin.json").get("mcpServers")),
        (".mcp.json", _load(".mcp.json").get("mcpServers")),
    ):
        assert isinstance(servers, dict), f"{source} must declare an mcpServers object"
        assert set(servers) == {EXPECTED_NAME}, f"{source} keys {sorted(servers)!r}, expected [{EXPECTED_NAME!r}]"
        assert servers[EXPECTED_NAME]["args"][-1] == EXPECTED_NAME, f"{source}: console entry must be the server key"


def test_server_package_declares_its_own_plugin_version():
    """The plugin version is per capability, NOT mcp_framework's release-train version."""
    init_py = os.path.join(CAP_DIR, IMPORT_NAME, "__init__.py")
    source = open(init_py, encoding="utf-8").read()
    m = re.search(r'^__version__ = "([^"]+)"$', source, re.MULTILINE)
    assert m, "__init__.py must assign a literal __version__ (not re-export mcp_framework's)"
    index = json.loads(open(os.path.join(REPO_ROOT, "plugin-versions.json"), encoding="utf-8").read())
    assert m.group(1) == index["plugins"][CAP], "server __version__ must equal the plugin version"


def test_capability_is_registered_for_release():
    index = json.loads(open(os.path.join(REPO_ROOT, "plugin-versions.json"), encoding="utf-8").read())
    assert CAP in index["plugins"], "capability must appear in plugin-versions.json"
    marketplace = json.loads(
        open(os.path.join(REPO_ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8").read()
    )
    entry = next((p for p in marketplace["plugins"] if p["name"] == EXPECTED_NAME), None)
    assert entry is not None, "capability must be listed in marketplace.json"
    assert entry["description"] == PUBLIC_DESCRIPTION
    expected_tag = index["tag_format"].format(cap=CAP, version=index["plugins"][CAP])
    assert entry["source"]["ref"] == expected_tag
    with open(os.path.join(REPO_ROOT, "install.sh"), encoding="utf-8") as f:
        installer = f.read()
    caps = re.search(r"^CAP_ITEMS=\(([^\n]+)\)$", installer, re.MULTILINE)
    assert caps and CAP in caps.group(1).split(), "capability must appear in install.sh CAP_ITEMS"
    assert PUBLIC_DESCRIPTION in installer
    pyproject = open(os.path.join(REPO_ROOT, "pyproject.toml"), encoding="utf-8").read()
    all_extra = re.search(r'^all = \["qwen-mm-plugins\[([^\]]+)\]"\]$', pyproject, re.MULTILINE)
    assert all_extra and CAP in all_extra.group(1).split(","), "capability must appear in the all extra"


def test_l3_review_modes_and_iteration_contract_agree():
    skill_root = os.path.join(CAP_DIR, "skill")
    paths = [
        SKILL_MD,
        os.path.join(skill_root, "references", "eval-and-iterate.md"),
        os.path.join(skill_root, "references", "eval-layers.md"),
        os.path.join(skill_root, "references", "schemas.md"),
    ]
    texts = [open(path, encoding="utf-8").read() for path in paths]
    for text in texts:
        assert "human-feedback" in text
        assert "agent-self-iterate" in text
        assert "headless-self-iterate" not in text
    assert "<run-tmp>/eval_run/iteration-<N>" in texts[1]
    assert "iteration_metadata.json" in texts[1]
    assert "iteration_metadata.json" in texts[3]
    assert "all active evals" in texts[1]
    assert "promotion_status" in texts[1]
    assert "promotion_status" in texts[3]
