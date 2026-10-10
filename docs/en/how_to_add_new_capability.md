# Add a new plugin

**English** · [中文](../zh/how_to_add_new_capability.md)

Adding a plugin involves two repositories:

1. In **Qwen-MM-Plugins**, implement and register `src/capabilities/<cap>/`, including its Skill,
   descriptions, manifests, tests, and optional MCP server.
2. In **[QwenLM/qwen-mm-plugins-hub](https://github.com/QwenLM/qwen-mm-plugins-hub)**, add the
   plugin's cookbook and public case files. The Hub generates the plugin page, tool reference,
   Skill preview, and token estimates; do not maintain another tool catalog there.

The Hub reads this repository's `main` branch. Its own content lives on Hub
`main`; the [publishing steps](hub.md#publish-and-refresh) explain how to update both.

Every published plugin includes `skill/SKILL.md`. An MCP server is optional: start from
[`example`](../../src/capabilities/example/) for a server plugin or
[`edu-agent`](../../src/capabilities/edu-agent/) for Skill-only packaging. Keep only the template
files your plugin needs. `example` itself is not published.

## Structure

```
src/capabilities/example/
├── skill/SKILL.md                  # Agent Skill (frontmatter: name/description + body)
└── qwen_mm_plugins_example/         # MCP server package (dir name == import name, must be a valid Python identifier)
    ├── __init__.py                 # __version__ + build_registry(__name__, ["tools"]) + SYSTEM_DEPS + list_tools
    ├── __main__.py                 # generic entry shim (copied verbatim from any server)
    └── tools/                      # one .py per tool, exporting TOOL + handle, auto-discovered at startup (dirs set by `build_registry`)
        ├── echo.py                 # returns plain text
        ├── swatch.py               # returns an image (solid-color PNG, via shared.content.image)
        ├── film_strip.py           # returns multi-frame images (the same pattern real video tools use to return frames)
        ├── describe.py             # calls an OpenAI-compatible API (endpoint/key via shared.env; dry_run offline)
        └── config_probe.py         # reads env/config via shared.env.get_env (env > config > default)
```

## Tool convention (auto-discovery)

All tools use the [same docstring convention](hub.md#author-descriptions-once):
`TOOL` declares only the name and Pydantic argument model. The handler's Google-style
docstring supplies the tool description and every argument description.

Create a new `.py` under `tools/` (or under the subpackage list defined by build_registry), exporting just two things:

```python
from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    message: str
    repeat: int = Field(default=1, ge=1, le=10)


TOOL = {"name": "echo", "args": EchoArgs}


def handle(arguments: dict) -> list[dict]:
    """Echo a message.

    Args:
        message: Text to echo back.
        repeat: Repeat count, from 1 to 10.
    """
    return [{"type": "text", "text": arguments["message"] * arguments.get("repeat", 1)}]
```

- `args` is a Pydantic model that auto-generates the tool's `inputSchema` and validates every call; `handle` receives a plain dict and returns MCP content blocks (`text` / `image`).
- Lazy import: as in `swatch.py`, import `PIL` inside `handle` so it doesn't affect other tools. System tools (ffmpeg, etc.) are declared in a `SYSTEM_DEPS` table in `__init__.py` (each entry needs only `label` + `tools` + `hint`; optional `extra`/`probe`/`startup` are documented in the SYSTEM_DEPS engine in `mcp_framework`); the framework uses it to uniformly render `--check-system` and warn at startup; an empty table = "No system tools required.".

## Run it / install it

After installing your Python dependencies in a virtual environment, test the server directly:

```bash
python3 src/capabilities/example/qwen_mm_plugins_example --version
python3 src/capabilities/example/qwen_mm_plugins_example --check-system
```

Replace the example path with your capability's path. For full Skill and MCP installation tests,
check out the generated first version PR in a dedicated clone and run `bash install.sh local`.
Restore manifests with `bash install.sh local --restore` before committing. See
[Local development](local_development.md).

## What to change

Use one capability ID throughout: folder `<yourname>`, plugin and Skill name
`qwen-mm-plugins-<yourname>`, extra `<yourname>`, and Python import
`qwen_mm_plugins_<yourname_with_underscores>`. Write a concise, task-specific Skill description
and H1; put prerequisites and workflow instructions in its body, with supporting files under
`skill/`.

For a server plugin, copy `src/capabilities/example/` to `src/capabilities/<yourname>/`, rename
its Python package, and complete steps 1–4. Skill-only plugins skip these Python packaging steps.

1. `pyproject.toml` `[project.scripts]` — add an entry:
   ```toml
   qwen-mm-plugins-<yourname> = "<import_name>.__main__:main"
   ```
2. `pyproject.toml` `[project.optional-dependencies]` — add an extra group (listing your pip deps):
   ```toml
   <yourname> = ["...your deps..."]
   ```
3. `pyproject.toml` `[tool.setuptools] package-dir` — map the import name to the directory:
   ```toml
   "<import_name>" = "src/capabilities/<yourname>/<import_name>"
   ```
4. `pyproject.toml` `[tool.setuptools.packages.find] where` — add the corresponding plugin directory
   (`include = ["qwen_mm_plugins*"]` already matches `qwen_mm_plugins_*`; if you use a different prefix, remember to update `include` too):
   ```toml
   where = [..., "src/capabilities/<yourname>"]
   ```
   Include the new server extra in the `all` profile. If it ships non-Python resources, add them
   to `[tool.setuptools.package-data]`.
5. Copy `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, and `.qoder-plugin/plugin.json`
   from a capability of the same kind. Update the name and description; keep a consistent placeholder version. For MCP plugins, also update `.mcp.json`
   with the plugin's server key, extra, and entry point; use `@main` during development and declare
   the server's own `__version__`. The Claude manifest's nonempty, single-line description is used
   in the marketplace and installer.
6. Leave `plugin-versions.json`, the root marketplace, and the installer's plugin lists unchanged.
   A maintainer requests `/release <yourname>=1.0.0` on the code PR; the bot generates the first
   version PR after merge. Review it and comment `/publish`. See [First release](releasing.md#first-release-of-a-new-plugin).
7. Add handler/schema tests or Skill/manifest tests as appropriate; see [Testing](testing.md).
   Update affected discovery and installer expectations. If you add configuration, register it
   in `src/shared/env.py:CONFIG_FIELDS`, align `install.sh:CONFIG_SPEC`, and regenerate the
   [configuration reference](configuration.md) with `scripts/gen_env_docs.py`.

`__main__.py` is **copied verbatim** from `src/capabilities/example/` — it infers the import name from the directory name and contains no per-server literals.
A skill-only capability omits `mcpServers` and `.mcp.json`, but keeps the three harness manifests.

## Add the Hub cookbook

In [QwenLM/qwen-mm-plugins-hub](https://github.com/QwenLM/qwen-mm-plugins-hub), create
`content/cookbooks/<yourname>/usage.md` and put demo files under
`public/cases/<yourname>/<case>/assert/`. An optional interactive case starts at `<case>/index.html`.
The [Hub authoring guide](hub.md#cookbook-and-cases) provides the Markdown template, contributor
metadata, and media-link conventions. A missing cookbook fails the content build.

Add a short entry to this repository's English and Chinese READMEs, linking its cookbook to
`https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/<yourname>/cookbook/`. Do not copy cookbook
Markdown or case media back into this repository. General guides remain in `docs/en/` and are
imported into the Hub automatically on its next build.

## Check and publish

Run the relevant [offline checks](testing.md#commands), then validate both repositories together
using the [Hub build commands](hub.md#validate-locally). The Hub exports the real registry; it
does not run handlers or contact model providers during the content build.

Follow [Publish and refresh](hub.md#publish-and-refresh) to make the documentation available.
Publishing a Hub preview does not make a new plugin installable through release-pinned
marketplaces. Complete the separate [plugin release process](releasing.md) before advertising a
stable installation.

## Reusing code from the shared library

There is already a shared library `src/shared/`:

Read configuration inside the operation that uses it, not in module-level assignments or tool-schema
defaults. Use `get_env`, `get_int_env`, `get_float_env`, `get_bool_env`, or `get_choice_env` from
`shared.env`. `CONFIG_FIELDS` groups ordinary settings as `(Pydantic type, Field(...))` entries:
declare the default, bounds/choices and description there once. Typed readers use those declarations,
so ordinary callers pass only the name; only capability-private fields supply a local default or bounds.
`config_catalog()` derives installer/documentation rows from that same schema. Missing/blank means
default, while an invalid explicit value raises `ConfigurationError`.
Resolve request settings before entering provider retries or fallback blocks, then reuse those
values for that request. This keeps configuration failures out of network-error handling.
Do not catch that exception to warn, clamp, retry a provider, or substitute a default. Let it reach
the SDK or return its message with the existing `text_error` helper; no special error flag is required.
Standalone scripts raise it directly. When a tool argument has a dynamic
configuration default, advertise `None` and resolve it in the handler. See
[configuration and migration rules](configuration.md#upgrading-to-20). Portable Skill readers use
the same Pydantic implementation; ensure `pydantic>=2.11,<3` is available in their interpreter, then
run `scripts/sync_env_readers.py --write` after changing the reader.

- `shared.env` — config/constants + `get_env` (the single call-time entry for reading env vars; precedence: environment > `~/.qwen-mm-plugins/config` > default) (`TOKEN_SIZE`, `DEFAULT_*`, `IMAGE_BUDGET_TOKENS`/`VIDEO_BUDGET_TOKENS`, `MAX_RESPONSE_BYTES`…)
- `shared.content` — input guards + error blocks (`text_error` / `require_file` / `require_dep` / `default_output_path`)
- `shared.image` — PIL image processing + resolution math (`draw_boxes`, `norm_to_pixel`, `save_image`, `budget_to_pixels`, `smart_resize`)
- `shared.video` — frame extraction / video info + timestamp parsing (`get_video_info`, `extract_frames_by_seeking`, `compute_dynamic_fps`, `parse_time`)
- `shared.cache` — derived-artifact caching (`cache_dir`, `cached_path`)
- `shared.syscmd` — locating external CLIs, incl. PATH restoration (`which_tool`, `find_tool`)
- `shared.isolated_worker` — run a JSON-serializable callable in a clean interpreter when native
  initialization, process-global state, or a hard timeout could destabilize the MCP server
  (`run_isolated`)
- `shared.api_openai` — OpenAI-compatible chat client (`call_openai_chat`, `resolve_openai_endpoint`)
- `shared.api_dashscope` — DashScope native-REST async generation tasks (`submit_dashscope_async`, `poll_dashscope_task`, `save_url_to_dir`, `retry_call`)

Use an isolated worker only when a call can deadlock or crash the interpreter, owns non-thread-safe
process-global state, or must be terminated on a hard timeout. Blocking I/O belongs on the normal
handler thread, and an already-isolated external CLI does not need another Python worker. Worker
arguments and results must be JSON-serializable. Isolation protects the MCP process and its stdio
transport; it is not a security sandbox or a CPU/memory permission boundary.
