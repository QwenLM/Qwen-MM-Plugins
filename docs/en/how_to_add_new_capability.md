# Adding a New Capability/Plugin

**English** · [中文](../zh/how_to_add_new_capability.md)

Under `src/`, each plugin = one short folder that may contain `skill/` (an Agent Skill) and/or `<import_name>/` (an MCP server package) — both optional. **The fastest way: copy the runnable template [`src/capabilities/example/`](../../src/capabilities/example/) and edit it.**

> For how to add tests after writing a plugin, see [Testing](testing.md) — conftest auto-discovers your server package, but a few steps such as the schema baseline must be added by hand.

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

Create a new `.py` under `tools/` (or under the subpackage list defined by build_registry), exporting just two things:

```python
from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    message: str = Field(description="Text to echo back.")
    repeat: int = Field(default=1, description="Repeat count (1-10).")


TOOL = {"name": "echo", "description": "...", "args": EchoArgs}


def handle(arguments: dict) -> list[dict]:
    ...
    return [{"type": "text", "text": ...}]  # or {"type": "image", "data": <base64>, "mimeType": ...}
```

- `args` is a Pydantic model that auto-generates the tool's `inputSchema` and validates every call; `handle` receives a plain dict and returns MCP content blocks (`text` / `image`).
- Lazy import: as in `swatch.py`, import `PIL` inside `handle` so it doesn't affect other tools. System tools (ffmpeg, etc.) are declared in a `SYSTEM_DEPS` table in `__init__.py` (each entry needs only `label` + `tools` + `hint`; optional `extra`/`probe`/`startup` are documented in the SYSTEM_DEPS engine in `mcp_framework`); the framework uses it to uniformly render `--check-system` and warn at startup; an empty table = "No system tools required.".

## Run it / install it

```bash
# run straight from source
python3 src/capabilities/example/qwen_mm_plugins_example --version
python3 src/capabilities/example/qwen_mm_plugins_example --check-system

# install into a harness — `example` ships as a template only (it is NOT listed in the
# marketplace); after you copy it to your own capability and add that to marketplace.json,
# install YOURS:
claude plugin marketplace add <local repo path or git URL>
claude plugin install qwen-mm-plugins-<your-cap>@qwen-mm-plugins
```

## What to change

Copy `src/capabilities/example/` to `src/capabilities/<yourname>/`, rename `qwen_mm_plugins_example/` to your import name, then:

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
5. Add the initial version to `plugin-versions.json` and a matching tag-pinned `git-subdir` entry to
   `.claude-plugin/marketplace.json`. Copy the three harness manifests from an existing capability;
   server capabilities also carry `.mcp.json`. Run `scripts/check_manifests.py`, then follow
   [Plugin releases](releasing.md) for the first tag.

`__main__.py` is **copied verbatim** from `src/capabilities/example/` — it infers the import name from the directory name and contains no per-server literals.
A skill-only capability omits `mcpServers` and `.mcp.json`, but keeps the three harness manifests.

## Reusing code from the shared library

There is already a shared library `src/shared/`:

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
