# Repository instructions for coding agents

Qwen-MM-Plugins is an Agent Skills + MCP Tools platform. User-facing orientation belongs in the
[README](README.md); installation, development, testing, and release behavior belong in `docs/`.
Keep this file limited to non-obvious repository invariants.

## Mandatory video workflow

When a user provides a video file (`.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`) or a directory of videos
and asks about its content:

1. Invoke the `qwen-mm-plugins-video-memory` Skill first.
2. Do not use ffmpeg/ffprobe to sample frames directly.
3. Do not answer a long-video question from a few thumbnails.

The Skill checks existing memory, builds it when necessary, and queries it.

## Repository invariants

- One capability lives under `src/capabilities/<cap>/` and may contain `skill/`, an MCP server
  package, or both. `edu-agent` is Skill-only; `example` is an unpublished template.
- Names stay aligned: folder `<cap>`, Skill/plugin/entry `qwen-mm-plugins-<cap>`, package extra
  `[<cap>]`, and Python import `qwen_mm_plugins_<cap-with-underscores>`.
- Reusable code belongs in `src/shared/` or `src/mcp_framework.py`. Never import a sibling
  capability's server package; installed capabilities are independent.
- All MCP servers ship in the single `qwen-mm-plugins` Python distribution. Extras choose
  dependencies, not packaged source files.
- Plugin releases are independent. `plugin-versions.json`, all harness manifests, marketplace refs,
  MCP package refs, and server `__version__` must agree for a capability. Shared changes require
  version bumps for every affected capability. Never move a published tag.
- Marketplace installs must bundle every component the capability ships. Server capabilities carry
  Skill + MCP; Skill-only capabilities must not advertise an MCP server.
- `src/shared/env.py` is the source of truth for ordinary configuration fields and defaults. Read
  runtime settings through `shared.env.get_env`, not `os.environ` directly.
- Python dependencies go in `pyproject.toml`. Non-Python applications go in the capability's
  `SYSTEM_DEPS` table so `--check-system` and startup warnings stay consistent.
- The video-memory builder keeps intentional copies of `schema.py` and `embeddings.py`; repository
  consistency tests require them to remain byte-identical to the server copies.

## MCP server convention

Each server package:

- exposes its per-capability `__version__`, `SPECS`, and `get_handler` from `__init__.py`;
- uses the generic `__main__.py` shim and `mcp_framework.run_main`;
- calls `build_registry(__name__, [subpackages])` to discover tools;
- declares each tool in a module exporting a Pydantic-backed `TOOL` dictionary and
  `handle(arguments) -> list[content-dict]`;
- returns MCP `text` or `image` content blocks and imports optional heavy dependencies lazily.

Do not manually duplicate tool registration or schema definitions. Qwen Code namespaces MCP
servers globally, so every manifest server key must use the unique capability name.

## Local development

Use `bash install.sh local` for the complete harness install path. It intentionally writes absolute
checkout paths into tracked manifests; use a dedicated clone and restore with:

```bash
scripts/dev-plugin.sh all --revert
```

Use direct Python execution for faster server-only iteration. See
[Local development](docs/en/local_development.md).

## Verification

Run targeted tests while working, then the offline checks relevant to the change:

```bash
python3 -m pytest -m "not reachability" tests/
python3 scripts/check_manifests.py
ruff format --check .
ruff check .
```

For shell changes, run `bash -n <script>`. Reachability tests are opt-in and require credentials;
do not make the default suite depend on public network access.

## References

- [Installation](docs/en/installation.md)
- [Local development](docs/en/local_development.md)
- [Adding a capability](docs/en/how_to_add_new_capability.md)
- [Testing](docs/en/testing.md)
- [Plugin releases](docs/en/releasing.md)
