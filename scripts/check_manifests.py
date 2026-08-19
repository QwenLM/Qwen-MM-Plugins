#!/usr/bin/env python3
"""Cross-check the release index, marketplace, capability manifests and pyproject.toml.

For every capability under src/capabilities/<cap>/ this verifies the naming convention
(capability name / plugin name / MCP-server key / console entry are all `qwen-mm-plugins-<cap>`)
and that the three registration surfaces agree:

- plugin-versions.json — canonical latest stable version and source ref for each published plugin;
  source_refs may temporarily override the immutable tag while a capability is tested pre-release.
- .claude-plugin/marketplace.json — each published plugin uses a git-subdir source pinned to its
  canonical source ref (normally an immutable tag); marketplace metadata matches mcp_framework.
- src/capabilities/<cap>/{.claude-plugin,.codex-plugin,.qoder-plugin}/plugin.json (+ .mcp.json) —
  same name and per-capability version; MCP launch specs pin the same capability tag. A server's
  `__version__` is the plugin version, independent of the one-distribution release-train version.
- pyproject.toml — [project.scripts] has `qwen-mm-plugins-<cap> = "<import_name>.__main__:main"`
  and [tool.setuptools.package-dir] maps <import_name> to its capability dir (and nothing stale).

Exit code: 0 when consistent, 1 with one line per problem otherwise. Stdlib-only (pyproject is
read with a minimal line parser — the checked tables hold only single-line `key = "value"` pairs).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPABILITIES_DIR = REPO_ROOT / "src" / "capabilities"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"
FRAMEWORK = REPO_ROOT / "src" / "mcp_framework.py"
VERSIONS = REPO_ROOT / "plugin-versions.json"
PREFIX = "qwen-mm-plugins-"

errors: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"{rel(path)}: unreadable or invalid JSON ({exc})")
        return None


def framework_version() -> str | None:
    m = re.search(r'^__version__ = "([^"]+)"', FRAMEWORK.read_text(encoding="utf-8"), re.MULTILINE)
    if not m:
        fail(f"{rel(FRAMEWORK)}: __version__ not found")
        return None
    return m.group(1)


def release_index() -> tuple[str | None, dict[str, str], str, dict[str, str]]:
    data = load_json(VERSIONS)
    if not isinstance(data, dict):
        return None, {}, "qwen-mm-plugins-{cap}-v{version}", {}
    distribution_version = data.get("distribution_version")
    versions = data.get("plugins")
    tag_format = data.get("tag_format")
    source_refs = data.get("source_refs", {})
    if not isinstance(distribution_version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", distribution_version
    ):
        fail(f"{rel(VERSIONS)}: distribution_version must be semver")
        distribution_version = None
    if not isinstance(versions, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in versions.items()
    ):
        fail(f"{rel(VERSIONS)}: plugins must be an object of capability -> version strings")
        versions = {}
    if not isinstance(tag_format, str) or "{cap}" not in tag_format or "{version}" not in tag_format:
        fail(f"{rel(VERSIONS)}: tag_format must contain {{cap}} and {{version}}")
        tag_format = "qwen-mm-plugins-{cap}-v{version}"
    for cap, version in versions.items():
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
            fail(f"{rel(VERSIONS)}: {cap} has invalid semver {version!r}")
    if not isinstance(source_refs, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"[0-9A-Za-z._/-]+", v)
        for k, v in source_refs.items()
    ):
        fail(f"{rel(VERSIONS)}: source_refs must be an object of capability -> Git ref strings")
        source_refs = {}
    unknown_refs = set(source_refs) - set(versions)
    if unknown_refs:
        fail(f"{rel(VERSIONS)}: source_refs contains unknown capabilities: {sorted(unknown_refs)}")
    return distribution_version, versions, tag_format, source_refs


def source_ref(cap: str, version: str, tag_format: str, source_refs: dict[str, str]) -> str:
    return source_refs.get(cap, tag_format.format(cap=cap, version=version))


def parse_toml_table(text: str, table: str) -> dict[str, str]:
    """Minimal single-line `key = "value"` parser for one [table] of pyproject.toml."""
    out: dict[str, str] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current == table and "=" in line:
            key, _, val = line.partition("=")
            out[key.strip().strip('"')] = val.strip().strip('"')
    return out


def server_package(cap_dir: Path) -> str | None:
    """The capability's MCP-server package dir (a valid import name with an __init__.py), if any."""
    for sub in sorted(cap_dir.iterdir()):
        if sub.is_dir() and sub.name.isidentifier() and (sub / "__init__.py").is_file():
            return sub.name
    return None


def entry_name_of(server_cfg: dict, expected: str, where: str) -> None:
    """Check one mcpServers entry: a uvx launch whose console entry (last arg) is the server key."""
    args = server_cfg.get("args")
    if not isinstance(args, list) or not args:
        fail(f"{where}: mcpServers entry has no args list")
        return
    if args[-1] != expected:
        fail(f"{where}: console entry is {args[-1]!r}, expected {expected!r}")


def check_launch_ref(server_cfg: dict, cap: str, tag: str, where: str) -> None:
    args = server_cfg.get("args")
    if not isinstance(args, list):
        return
    if "--from" not in args:
        fail(f"{where}: MCP launch args must contain --from")
        return
    try:
        spec = args[args.index("--from") + 1]
    except IndexError:
        fail(f"{where}: --from has no package spec")
        return
    expected_extra = f"qwen-mm-plugins[{cap}]"
    if expected_extra not in spec or not spec.endswith(f"@{tag}"):
        fail(f"{where}: --from must use [{cap}] and end in @{tag}, got {spec!r}")


def check_mcp_servers(mcp_servers, expected_name: str, where: str) -> dict | None:
    if not isinstance(mcp_servers, dict) or set(mcp_servers) != {expected_name}:
        keys = sorted(mcp_servers) if isinstance(mcp_servers, dict) else mcp_servers
        fail(f"{where}: mcpServers keys {keys!r}, expected exactly [{expected_name!r}]")
        return None
    server_cfg = mcp_servers[expected_name]
    if not isinstance(server_cfg, dict):
        fail(f"{where} → {expected_name}: server configuration must be an object")
        return None
    entry_name_of(server_cfg, expected_name, f"{where} → {expected_name}")
    return server_cfg


def check_capability(
    cap_dir: Path,
    version: str | None,
    tag_format: str,
    source_refs: dict[str, str],
    scripts: dict[str, str],
    package_dir: dict[str, str],
) -> None:
    cap = cap_dir.name
    expected_name = PREFIX + cap
    import_name = server_package(cap_dir)

    manifests = {
        "claude": cap_dir / ".claude-plugin" / "plugin.json",
        "codex": cap_dir / ".codex-plugin" / "plugin.json",
        "qoder": cap_dir / ".qoder-plugin" / "plugin.json",
    }
    mcp_json_path = cap_dir / ".mcp.json"
    loaded: dict[str, dict] = {}
    for kind, path in manifests.items():
        if not path.is_file():
            fail(f"{rel(cap_dir)}: missing {path.relative_to(cap_dir)}")
            continue
        data = load_json(path)
        if data is None:
            continue
        loaded[kind] = data
        if data.get("name") != expected_name:
            fail(f"{rel(path)}: name {data.get('name')!r}, expected {expected_name!r}")
        if version is not None and data.get("version") != version:
            fail(f"{rel(path)}: version {data.get('version')!r} != release index {version!r}")

    manifest_versions = {kind: data.get("version") for kind, data in loaded.items()}
    if len(set(manifest_versions.values())) > 1:
        fail(f"{rel(cap_dir)}: manifest versions differ: {manifest_versions}")

    if import_name is not None:
        # MCP-server capability: server key + entry consistent everywhere, and registered in pyproject.
        if "claude" in loaded:
            cfg = check_mcp_servers(loaded["claude"].get("mcpServers"), expected_name, f"{rel(manifests['claude'])}")
            if cfg is not None and version is not None:
                ref = source_ref(cap, version, tag_format, source_refs)
                check_launch_ref(cfg, cap, ref, rel(manifests["claude"]))
        if not mcp_json_path.is_file():
            fail(f"{rel(cap_dir)}: server capability without .mcp.json (codex/qoder manifests need it)")
        else:
            mcp_json = load_json(mcp_json_path)
            if mcp_json is not None:
                cfg = check_mcp_servers(mcp_json.get("mcpServers"), expected_name, rel(mcp_json_path))
                if cfg is not None and version is not None:
                    ref = source_ref(cap, version, tag_format, source_refs)
                    check_launch_ref(cfg, cap, ref, rel(mcp_json_path))
        if "codex" in loaded and loaded["codex"].get("mcpServers") != "./.mcp.json":
            fail(f"{rel(manifests['codex'])}: mcpServers should reference './.mcp.json'")
        if "qoder" in loaded and loaded["qoder"].get("mcp") != ".mcp.json":
            fail(f"{rel(manifests['qoder'])}: mcp should reference '.mcp.json'")
        if scripts.get(expected_name) != f"{import_name}.__main__:main":
            fail(
                f"pyproject.toml [project.scripts]: {expected_name} = {scripts.get(expected_name)!r}, "
                f"expected '{import_name}.__main__:main'"
            )
        expected_pkg_dir = f"src/capabilities/{cap}/{import_name}"
        if package_dir.get(import_name) != expected_pkg_dir:
            fail(
                f"pyproject.toml [tool.setuptools.package-dir]: {import_name} = "
                f"{package_dir.get(import_name)!r}, expected {expected_pkg_dir!r}"
            )
        if version is not None:
            init_text = (cap_dir / import_name / "__init__.py").read_text(encoding="utf-8")
            match = re.search(r'^__version__ = "([^"]+)"', init_text, re.MULTILINE)
            if not match or match.group(1) != version:
                actual = match.group(1) if match else None
                fail(f"{rel(cap_dir / import_name / '__init__.py')}: __version__ {actual!r} != {version!r}")
    else:
        # Skill-only capability: must not declare an MCP server anywhere.
        if "claude" in loaded and "mcpServers" in loaded["claude"]:
            fail(f"{rel(manifests['claude'])}: skill-only capability declares mcpServers")
        if expected_name in scripts:
            fail(f"pyproject.toml [project.scripts]: {expected_name} registered but {cap} has no server package")


def main() -> int:
    framework_release = framework_version()
    distribution_version, versions, tag_format, source_refs = release_index()
    if distribution_version is not None and framework_release != distribution_version:
        fail(
            f"{rel(FRAMEWORK)}: __version__ {framework_release!r} != "
            f"release index distribution_version {distribution_version!r}"
        )
    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    scripts = parse_toml_table(pyproject_text, "project.scripts")
    package_dir = parse_toml_table(pyproject_text, "tool.setuptools.package-dir")
    caps = sorted(d for d in CAPABILITIES_DIR.iterdir() if d.is_dir())

    # Marketplace ↔ capability dirs.
    marketplace = load_json(MARKETPLACE)
    listed: set[str] = set()
    if marketplace is not None:
        meta_version = marketplace.get("metadata", {}).get("version")
        if distribution_version is not None and meta_version != distribution_version:
            fail(
                f"{rel(MARKETPLACE)}: metadata.version {meta_version!r} != "
                f"release index distribution_version {distribution_version!r}"
            )
        names = [p.get("name") for p in marketplace.get("plugins", [])]
        for name in {n for n in names if names.count(n) > 1}:
            fail(f"{rel(MARKETPLACE)}: duplicate plugin name {name!r}")
        for plugin in marketplace.get("plugins", []):
            name, source = plugin.get("name"), plugin.get("source")
            if not isinstance(source, dict) or source.get("source") != "git-subdir":
                fail(f"{rel(MARKETPLACE)}: {name}: source must be a git-subdir object")
                continue
            source_path = source.get("path", "")
            src_dir = (REPO_ROOT / source_path).resolve()
            cap = src_dir.name
            listed.add(cap)
            if name != PREFIX + cap:
                fail(f"{rel(MARKETPLACE)}: plugin {name!r} does not match source dir name ({PREFIX}{cap!s})")
            if not src_dir.is_dir():
                fail(f"{rel(MARKETPLACE)}: {name}: source path {source_path!r} does not exist")
            elif not (src_dir / ".claude-plugin" / "plugin.json").is_file():
                fail(f"{rel(MARKETPLACE)}: {name}: source has no .claude-plugin/plugin.json")
            version = versions.get(cap)
            if version is None:
                fail(f"{rel(MARKETPLACE)}: {name}: missing from {rel(VERSIONS)}")
            else:
                expected_ref = source_ref(cap, version, tag_format, source_refs)
                if source.get("ref") != expected_ref:
                    fail(f"{rel(MARKETPLACE)}: {name}: ref {source.get('ref')!r}, expected {expected_ref!r}")

    # Per-capability manifests ↔ pyproject.
    for cap_dir in caps:
        check_capability(cap_dir, versions.get(cap_dir.name), tag_format, source_refs, scripts, package_dir)
        if cap_dir.name not in listed:
            notes.append(f"note: {rel(cap_dir)} is not listed in {rel(MARKETPLACE)} (intentional for the template)")

    if set(versions) != listed:
        fail(
            f"{rel(VERSIONS)} plugins must match marketplace capabilities: "
            f"index={sorted(versions)}, marketplace={sorted(listed)}"
        )

    # Reverse direction: nothing stale in pyproject.
    cap_names = {d.name for d in caps}
    for entry in scripts:
        if not entry.startswith(PREFIX) or entry[len(PREFIX) :] not in cap_names:
            fail(f"pyproject.toml [project.scripts]: {entry!r} has no capability dir under src/capabilities/")
    for import_name, target in package_dir.items():
        if import_name.startswith("qwen_mm_plugins") and not (REPO_ROOT / target).is_dir():
            fail(f"pyproject.toml [tool.setuptools.package-dir]: {import_name} -> {target!r} does not exist")

    for note in notes:
        print(note)
    if errors:
        print(f"\nFAIL — {len(errors)} inconsistency(ies):", file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 1
    print(f"OK — {len(caps)} capabilities consistent across marketplace.json, plugin manifests and pyproject.toml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
