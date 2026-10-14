# Testing

**English** · [中文](../zh/testing.md)

Tests are organized by behavior rather than by a fixed file catalog:

- **Unit and handler tests** cover schemas, tool discovery, transformations, and error paths.
- **Protocol tests** exercise initialize → `tools/list` → `tools/call` through a real MCP client.
- **Repository consistency** protects duplicated manifests, release refs, package versions, and
  intentionally copied source files.
- **Reachability tests** call external providers and are opt-in.

`tests/conftest.py` adds `src/` and server capability directories to `sys.path`, so a new MCP server
package is discovered without installing a wheel. Skill-only capabilities test their Skill and
manifest contract instead of a server surface.

## Commands

Run the offline suite used by CI:

```bash
python3 -m pytest -m "not reachability" tests/
python3 scripts/check_manifests.py
ruff format --check .
ruff check .
```

Run provider tests only when you intend to make live requests and have configured credentials:

```bash
QWEN_MM_RUN_REACHABILITY=1 python3 -m pytest -m reachability tests/
```

For shell changes, also run `bash -n <script>`. During development, target the relevant test module
or use `-k <pattern>` before running the complete offline suite.

## What to test

Match tests to the capability's components:

1. Every MCP server needs schema/discovery and handler success/error coverage.
2. Add protocol tests for server-specific startup, streaming, or transport behavior.
3. Add small, deterministic committed fixtures when readers or renderers need representative input.
   Keep large third-party snapshots outside the repository and use them only for opt-in manual tests.
4. Add an anti-drift assertion when two files or manifest fields must stay synchronized.
5. Skill-only changes should validate frontmatter, referenced resources, and manifest packaging.

Never require credentials, a GUI application, GPU hardware, or public-network reachability in the
default offline suite.
