# Installation

**English** · [中文](../zh/installation.md)

## Choose a source

| Goal | Command | Source |
|---|---|---|
| Install a release | `bash install.sh install` | Latest released tag for each capability |
| Update an existing install | `bash install.sh update` | Current release catalog |
| Test unpublished code | `bash install.sh local` | Current checkout, including uncommitted changes |
| Roll back one capability | `QMP_REF=<tag> bash install.sh install` | Exact immutable tag |

Released installs never follow `main`. To test a branch, check it out and use `local`.

## Guided installer

The installer supports Claude Code, Codex, Qoder, OpenClaw, Qwen Code, and Gemini CLI. It invokes
each harness's native install mechanism and stores shared configuration in
`~/.qwen-mm-plugins/config`.

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
| Codex | Start a new task, or restart |
| Qoder | `/plugins reload`, or restart |
| OpenClaw | Managed Gateways normally restart automatically; otherwise `openclaw gateway restart` |
| Qwen Code | Restart |
| Gemini CLI | `/skills reload` and `/mcp reload`, or restart |

### Roll back

Select only the capability named by the tag:

```bash
QMP_REF=qwen-mm-plugins-search-v1.0.1 bash install.sh install
```

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

Use this path for opencode, pi, QwenPaw, or another harness without a compatible marketplace. For
an MCP capability, keep these three values aligned:

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

Exact configuration examples for each manual harness are in
[Manual harness setup](manual_harnesses.md).

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
| `DASHSCOPE_API_KEY` | Cloud media APIs, generation, and video-memory builds |
| `SERPER_API_KEY` | Serper web search/extraction and all reverse-image search |
| `TAVILY_API_KEY` | Tavily web search and extraction |
| `EXA_API_KEY` | Exa web search and extraction |

Native `core` file reading needs no API key. Set values through the installer's **Configure** action,
the shell environment, or `~/.qwen-mm-plugins/config`; environment variables take precedence.
With `QWEN_MM_SEARCH_BACKEND` unset or set to `auto`, text search uses the first configured key in
this fixed order: Serper, Tavily, Exa. Setting it to `serper`, `tavily`, or `exa` pins that provider;
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
| Blender / FreeCAD | Their respective live application integrations |

Run `bash install.sh verify` or `<entry> --check-system` to see what the selected capability needs.
Capability-specific prerequisites are documented in its Skill and cookbook.

### Complete configuration

See the [configuration reference](configuration.md) for provider endpoints, search routing,
timeouts, cache paths, video-memory files, OSS, application hosts, and advanced compatibility
switches.
