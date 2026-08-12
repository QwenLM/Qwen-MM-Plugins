"""Guards against silent drift between intentionally-duplicated files.

- The video-memory build pipeline keeps byte-identical copies of schema.py and
  embeddings.py (it runs as flat modules, separate from the installed server
  package). These must not diverge.
- Published capability versions, marketplace refs, manifests and MCP launch refs all agree with
  plugin-versions.json. The Python distribution version is deliberately separate.
"""

import json
import pathlib

import pytest
from conftest import REPO_ROOT

_ROOT = pathlib.Path(REPO_ROOT)
_CAP = _ROOT / "src" / "capabilities" / "video-memory"
_SERVER_DIR = _CAP / "qwen_mm_plugins_video_memory"
_BUILD_DIR = _CAP / "skill" / "script" / "build_memory"


@pytest.mark.parametrize("fname", ["schema.py", "embeddings.py"])
def test_build_copies_stay_identical(fname):
    server_copy = _SERVER_DIR / fname
    build_copy = _BUILD_DIR / fname
    assert server_copy.read_bytes() == build_copy.read_bytes(), (
        f"{fname} drifted between the video-memory server package and its build-pipeline copy.\n"
        f"  server: {server_copy}\n  build:  {build_copy}\n"
        f"They must stay byte-identical (the builder writes what the server reads). Re-sync them, "
        f"or, if you intentionally forked them, drop this guard."
    )


# ── manifest sync across the four harness formats + marketplace (F2) ──
# Each capability hand-maintains .claude-plugin / .codex-plugin / .qoder-plugin / .mcp.json.
# These guard that name, version, and the uvx launch spec stay in agreement across a capability's
# own harness manifests (description is intentionally per-harness display text and is NOT checked).
# Versions are PER-CAPABILITY and independent of the distribution mcp_framework.__version__. Every
# released code/skill/manifest change bumps that capability and moves its immutable tag; no published
# launch spec follows main.

_CAPS_DIR = _ROOT / "src" / "capabilities"
_RELEASE_INDEX = json.loads((_ROOT / "plugin-versions.json").read_text())
_VERSIONS = _RELEASE_INDEX["plugins"]
_TAG_FORMAT = _RELEASE_INDEX["tag_format"]


def _capabilities() -> list[str]:
    return sorted(p.parent.parent.name for p in _CAPS_DIR.glob("*/.claude-plugin/plugin.json"))


def _load(cap: str, rel: str) -> dict:
    return json.loads((_CAPS_DIR / cap / rel).read_text())


def _server_capabilities() -> list[str]:
    """Caps that ship an MCP server — i.e. carry a .mcp.json launch spec. Skill-only caps
    (e.g. edu-agent) don't, so the launch-spec reconciliation below doesn't apply to them."""
    return [c for c in _capabilities() if (_CAPS_DIR / c / ".mcp.json").exists()]


@pytest.mark.parametrize("cap", _capabilities())
def test_manifest_name_and_version_agree(cap):
    manifests = {
        "claude": _load(cap, ".claude-plugin/plugin.json"),
        "codex": _load(cap, ".codex-plugin/plugin.json"),
        "qoder": _load(cap, ".qoder-plugin/plugin.json"),
    }
    names = {m["name"] for m in manifests.values()}
    assert len(names) == 1, f"{cap}: plugin name differs across harness manifests: {names}"
    # Version is per-capability (drives `plugin update`) and independent of the distribution
    # mcp_framework.__version__; only require a cap's three harness manifests to agree with each other.
    versions = {label: m["version"] for label, m in manifests.items()}
    assert len(set(versions.values())) == 1, (
        f"{cap}: plugin version differs across its harness manifests: {versions}. "
        "Bump a capability's claude/codex/qoder manifests together."
    )
    if cap in _VERSIONS:
        assert next(iter(versions.values())) == _VERSIONS[cap]


@pytest.mark.parametrize("cap", _server_capabilities())
def test_mcp_launch_spec_agrees(cap):
    # codex/qoder reference ./.mcp.json; the .claude-plugin inlines the same server. They must
    # match. The two files label the sole server differently (.claude-plugin uses "main", .mcp.json
    # uses the capability name), so compare the single server entry's launch args by value.
    def _sole_server_args(spec: dict) -> list:
        servers = spec["mcpServers"]
        assert len(servers) == 1, f"{cap}: expected exactly one mcpServers entry, got {sorted(servers)}"
        return next(iter(servers.values()))["args"]

    claude_args = _sole_server_args(_load(cap, ".claude-plugin/plugin.json"))
    mcp_args = _sole_server_args(_load(cap, ".mcp.json"))
    assert claude_args == mcp_args, (
        f"{cap}: uvx launch args drifted between .claude-plugin (inline) and .mcp.json:\n"
        f"  claude: {claude_args}\n  mcp:    {mcp_args}"
    )
    if cap in _VERSIONS:
        tag = _TAG_FORMAT.format(cap=cap, version=_VERSIONS[cap])
        assert claude_args[claude_args.index("--from") + 1].endswith(f"@{tag}")


def test_marketplace_lists_every_capability():
    import mcp_framework

    market = json.loads((_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    listed = {p["name"] for p in market["plugins"]}
    # `example` is a copy-me template that ships in the repo but is intentionally NOT published to
    # the marketplace (end users shouldn't see a demo plugin). Every other capability must be listed.
    TEMPLATE_ONLY = {"qwen-mm-plugins-example"}
    from_manifests = {_load(cap, ".claude-plugin/plugin.json")["name"] for cap in _capabilities()}
    assert listed == from_manifests - TEMPLATE_ONLY, (
        "marketplace.json plugins must match the capability manifests (minus template-only caps).\n"
        f"  marketplace: {sorted(listed)}\n  manifests:   {sorted(from_manifests)}\n"
        f"  template-only (excluded): {sorted(TEMPLATE_ONLY)}"
    )
    assert market["metadata"]["version"] == mcp_framework.__version__, (
        "marketplace.json metadata.version must track mcp_framework.__version__."
    )
    assert _RELEASE_INDEX["distribution_version"] == mcp_framework.__version__, (
        "plugin-versions.json distribution_version must track mcp_framework.__version__."
    )
    assert set(_VERSIONS) == {name.removeprefix("qwen-mm-plugins-") for name in listed}
    for plugin in market["plugins"]:
        cap = plugin["name"].removeprefix("qwen-mm-plugins-")
        source = plugin["source"]
        assert source["source"] == "git-subdir"
        assert source["path"] == f"src/capabilities/{cap}"
        assert source["ref"] == _TAG_FORMAT.format(cap=cap, version=_VERSIONS[cap])
