# Repository instructions for coding agents

Keep this file limited to non-obvious repository rules. User-facing orientation belongs in the
[README](README.md); installation, development, testing, and release details belong in `docs/`.

## Local development

Use `bash install.sh local` for the complete harness path and a dedicated clone: local mode writes
absolute checkout paths into tracked manifests. Restore them with `bash install.sh local --restore`.
For server-only iteration, run Python directly; see
[Local development](docs/en/local_development.md).

## Architecture and packaging

- A capability lives under `src/capabilities/<cap>/` and may ship a Skill, an MCP server, or both.
  `edu-agent` is Skill-only; `example` is an unpublished template.
- Keep names aligned: folder `<cap>`, Skill/plugin/entry `qwen-mm-plugins-<cap>`, extra `[<cap>]`,
  and import `qwen_mm_plugins_<cap-with-underscores>`.
- Installed capabilities are independent. Put reusable code in `src/shared/` or
  `src/mcp_framework.py`; never import a sibling capability's server package.
- All MCP servers ship in one `qwen-mm-plugins` distribution. Extras select dependencies, not
  packaged source files.
- Marketplace installs bundle every shipped component: server capabilities carry Skill + MCP;
  Skill-only capabilities must not advertise an MCP server.
- The video-memory builder intentionally copies `schema.py` and `embeddings.py`; consistency tests
  require the builder and server copies to remain byte-identical.

## MCP server convention

Each server package:

- exports its capability `__version__`, `SPECS`, and `get_handler` from `__init__.py`;
- uses the generic `__main__.py` shim, `mcp_framework.run_main`, and
  `build_registry(__name__, [subpackages])` discovery;
- declares each tool as a Pydantic-backed `TOOL` plus
  `handle(arguments) -> list[content-dict]`;
- returns MCP `text` or `image` blocks and imports optional heavy dependencies lazily.

Do not duplicate registration or schemas manually. Qwen Code namespaces MCP servers globally, so
every manifest server key must use the unique capability name.

## Configuration and dependencies

- Read runtime settings through `shared.env.get_env`, not `os.environ` directly.
  `src/shared/env.py:CONFIG_FIELDS` is the source of truth for ordinary fields and defaults.
- After changing `CONFIG_FIELDS`, align `install.sh:CONFIG_SPEC` and run
  `python3 scripts/gen_env_docs.py --write docs/en/configuration.md`; tests enforce both catalogs.
- Put Python dependencies in `pyproject.toml`. Declare non-Python applications in the capability's
  `SYSTEM_DEPS` so `--check-system` and startup warnings remain consistent.

## Release invariants

Capabilities release independently. For each capability, `plugin-versions.json`, harness
manifests, marketplace and MCP package refs, and server `__version__` must agree. Shared changes
require bumps for every affected capability. Never move a published tag.

## Verification

Run targeted tests while working, then the relevant offline checks:

```bash
python3 -m pytest -m "not reachability" tests/
python3 scripts/check_manifests.py
ruff format --check .
ruff check .
```

For shell changes, run `bash -n <script>`. Reachability tests are credentialed and opt-in; the
default suite must remain offline.

## References

- [Installation](docs/en/installation.md) · [Configuration](docs/en/configuration.md)
- [Local development](docs/en/local_development.md) · [Testing](docs/en/testing.md)
- [Adding a capability](docs/en/how_to_add_new_capability.md) · [Plugin releases](docs/en/releasing.md)
