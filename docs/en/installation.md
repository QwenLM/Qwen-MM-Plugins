# Installation

**English** · [中文](../zh/installation.md)

## Choose a source

| Goal | Command | Source |
|---|---|---|
| Install a release | `bash install.sh install` | Latest released tag for each capability |
| Update an existing install | `bash install.sh update` | Current release catalog |
| Test unpublished code | `bash install.sh local` | Current checkout, including uncommitted changes |
| Roll back one capability | See [Roll back](#roll-back) | Exact immutable tag |

Released installs never follow `main`. To test a branch, check it out and use `local`.

## Guided installer

The installer supports Claude Code, CodeBuddy, Codex, Qoder, OpenClaw, Qwen Code, and Gemini CLI. It
invokes each harness's native installation mechanism and stores shared configuration in
`~/.qwen-mm-plugins/config`.

`Qoder` and `Qwen Code` here do not include the separate QoderWork and QwenWork desktop apps. Use
their [in-app setup](manual_harnesses.md#qoderwork-and-qwenwork-in-app-task) instead.

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash
```

The menu provides **Install**, **Update**, **Configure**, **Verify**, and **Uninstall**. Each
capability is installed separately as a Skill plus an optional MCP server.

### Update

Use a current copy of the script so it contains the latest release catalog:

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash -s -- update
```

For managed installs, the selected capability's Skill and MCP configuration are updated together.
The installer then starts the tagged MCP package with `--check-system`. An already-open harness may
still need a reload:

| Harness | Activate the update |
|---|---|
| Claude Code | `/reload-plugins`, or restart |
| CodeBuddy | `/reload-plugins`, or restart |
| Codex | Start a new task, or restart |
| Qoder | `/plugins reload`, or restart |
| OpenClaw | Managed Gateways normally restart automatically; otherwise `openclaw gateway restart` |
| Qwen Code | Restart |
| Gemini CLI | `/skills reload` and `/mcp reload`, or restart |

### Roll back

For guided harnesses that accept a remote tag, select only the capability named by the tag:

```bash
QMP_REF=qwen-mm-plugins-search-v1.0.1 bash install.sh install
```

## Non-interactive installation and configuration

Every action supports both a guided flow and explicit non-interactive arguments:

```bash
bash install.sh local --plugin core --harness codex
bash install.sh install --plugin core,search --harness claude
bash install.sh local --plugin qwen-mm-plugins-core --plugin search --harness qwen-code --dry-run
bash install.sh update --plugin all --harness codex
bash install.sh uninstall --plugin search --harness codex
bash install.sh verify --plugin core,search
bash install.sh verify --harness codex
bash install.sh configure DASHSCOPE_API_KEY="$DASHSCOPE_API_KEY" QWEN_MM_NATIVE_MODE=1
bash install.sh configure 'QWEN_MM_CACHE=/path/with spaces/cache'
bash install.sh configure QWEN_MM_CACHE=  # clear the override and restore the default
```

`--plugin` accepts short capability names, full plugin IDs, comma-separated names, repeated options,
or `all`. `--harness` selects one of `claude`, `codebuddy`, `codex`, `qoder`, `openclaw`, `qwen-code`,
or `gemini`. Run `bash install.sh --help` for the current plugin catalog.

`install`, `local`, `update`, and `uninstall` require both selectors for non-interactive use.
For `update` and `uninstall`, `all` targets only plugins installed in the selected harness; an
explicitly named plugin that is not installed causes an error before changes begin. Non-interactive
uninstall preserves shared configuration and caches, and removes an empty Claude/CodeBuddy
marketplace only after every selected plugin has been removed successfully.

`verify --plugin <names>` checks the requested MCP packages without needing a harness.
`verify --harness <name>` checks that harness's installed plugins; combine both options to check
an installed subset. The older `--verify [caps]` form remains supported.

Explicit selections execute immediately without installer prompts or a terminal. Install the
harness CLI and `uv`/`uvx` first (`uvx` is unnecessary for uninstall, dry-runs, or Skill-only plugins).
Missing dependencies, invalid arguments, failed native commands, and failed MCP startup checks
return a nonzero exit status. Add `--dry-run` to install, local, update, uninstall, or verify to
print commands without executing changes or system checks; inventory/marketplace queries may
still run. Commands without arguments keep their guided flow.

`configure` without arguments opens the configuration menu. Pass one or more `KEY=VALUE` arguments from the
[configuration catalog](configuration.md#configure-catalog). It validates the entire batch before
writing, preserves unrelated settings, and never prints values. Quote arguments containing spaces;
values may contain `=` but must fit on one line. An empty value removes the setting. The shared
file keeps mode `600`; `QWEN_MM_CONFIG` and `QWEN_MM_CONFIG_DIR` select its location, and environment
variables still override saved settings. Non-interactive installation skips the configuration menu.

## Local checkout

Use a dedicated clone whose path will remain stable:

```bash
git clone https://github.com/QwenLM/Qwen-MM-Plugins.git
cd Qwen-MM-Plugins
git switch <development-branch>   # optional
bash install.sh local
```

Local mode points the selected plugin manifests and MCP package specs at this checkout and adds
`uvx --refresh`. It intentionally leaves absolute local paths in tracked manifests while the clone
is used for development. Restore release sources when leaving local mode:

```bash
bash install.sh local --restore
```

See [Local development](local_development.md) for direct source execution and targeted debugging.

## Manual Skill + MCP installation

Use this path for DeepSeek Harness, Hermes Agent, opencode, pi, QwenPaw, or another harness without
a compatible marketplace. For an MCP capability, keep these three values aligned:

- Skill: `src/capabilities/<cap>/skill`
- package extra: `qwen-mm-plugins[<cap>]`
- entry: `qwen-mm-plugins-<cap>`

For example, `video-memory` uses `[video-memory]`, not `[memory]`. `edu-agent` is Skill-only.

```bash
# Install/copy/link the Skill directory where your harness discovers Skills, then register:
uvx --from \
  "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" \
  qwen-mm-plugins-<cap>
```

Manual Skill and MCP registrations have no shared install receipt, so the harness usually cannot
detect or notify you about a mismatched update. Run the current installer, choose
**Update → other (manual / another harness)**, and update both registrations to the tag it prints.
For a linked Skill, use a dedicated checkout per capability/tag because independent tags may point
to different commits.

In-app steps and exact configuration examples for the other harnesses are in
[Other harness setup](manual_harnesses.md).

## Windows (WSL2)

Windows is currently supported through Ubuntu WSL2 only. Clone into the WSL home directory (for
example `~/code`), not a mounted Windows path such as `/mnt/c`, and run the Linux commands there.

```powershell
wsl --install -d Ubuntu
```

For Codex, select a WSL2 agent environment and install the plugin inside that same environment.
Native Windows has not been validated.

## Dependencies

`uvx` installs each capability's Python dependencies into an isolated cache. The remaining inputs
are service settings and system applications.

### Common service settings

These are the settings most users need for cloud capabilities. The
[configuration reference](configuration.md) covers every setting available through **Configure**.

| Variable | Used by |
|---|---|
| `DASHSCOPE_API_KEY` | Cloud media APIs, text-only image captions, generation, and memory builds (video-memory, omni-memory) |
| `SERPER_API_KEY` | Serper web search/extraction and all reverse-image search |
| `TAVILY_API_KEY` | Tavily web search and extraction |
| `EXA_API_KEY` | Exa web search and extraction |
| `SERPLY_API_KEY` | Serply web search and extraction |

Native `core` file reading needs no API key. Set values through the installer's **Configure** action,
the shell environment, or `~/.qwen-mm-plugins/config`; environment variables take precedence.
With the official DashScope endpoint, oversized local media used by Omni is uploaded to temporary OSS
when possible, without requiring user-managed OSS credentials. Existing fallback behavior remains
available when temporary upload cannot be used.

With `QWEN_MM_SEARCH_BACKEND` unset or set to `auto`, text search uses the first configured key in
this fixed order: Serper, Tavily, Exa, Serply. Setting it to `serper`, `tavily`, `exa`, or `serply` pins
that provider;
a missing key then raises an error instead of falling back.
`image_search` always uses Serper Lens, independently of `QWEN_MM_SEARCH_BACKEND`, and raises an
error when `SERPER_API_KEY` is unavailable.

### Common system tools

| Tool | Used by |
|---|---|
| `ffmpeg` | Video/audio reading, memory, editing, and rendering |
| LibreOffice | Office and DrawIO visualization |
| TeX | LaTeX visualization |
| Chromium | Web-page screenshots and edu-agent rendering |
| `tesseract` | On-screen text OCR when extracting a skill from a video (optional) |
| Blender / FreeCAD | Their respective live application integrations |

Run `bash install.sh verify` or `<entry> --check-system` to see what the selected capability needs.
Capability-specific prerequisites are documented in its Skill and cookbook.

### Complete configuration

See the [configuration reference](configuration.md) for provider endpoints, search routing,
timeouts, cache paths, video-memory files, OSS, application hosts, and advanced compatibility
switches.
