# Installation (detailed)

The fast paths are in the [README](../../README.md#-installation). This page covers local checkout
mode, manual harness setup, dependencies, and repository layout.

## Windows (WSL2)

Windows x64 users should install WSL2 with Ubuntu and clone the repository inside the WSL
home directory (for example `~/code`), rather than under a mounted Windows drive such as
`/mnt/c`. Then follow the same installation commands as Linux/macOS. From an elevated
PowerShell terminal, WSL2 can be installed with:

```powershell
wsl --install -d Ubuntu
```

When using Codex on Windows, set the agent environment to WSL2, restart Codex, and install
and use the plugin inside that same WSL environment. WSL2 is currently the only supported
Windows environment; native Windows has not yet been validated.

## Local checkout

Run local installation from a clone whose root contains `pyproject.toml`:

```bash
git clone https://github.com/QwenLM/Qwen-MM-Plugins.git
cd Qwen-MM-Plugins
bash install.sh local
```

The command uses the normal harness/capability picker, switches only the selected marketplace and
MCP sources to this checkout, and then runs the harness's native install command. Use a dedicated
clone and keep it at the same path; the localized manifests stay changed while that install is in
use. `scripts/dev-plugin.sh <cap>` uses the same rewriter with `uvx --refresh` for one-capability
development. Restore the manifests only when leaving local mode:

```bash
scripts/dev-plugin.sh all --revert
```

## Non-marketplace harnesses: register skill + MCP directly

Harnesses without a plugin marketplace register the **skill** and **MCP server** in their own config. **Qwen Code** and **Gemini CLI** are automated by the [guided installer](../../README.md#-installation) (`bash install.sh` → pick the harness); the rest (opencode, pi, QwenPaw, …) are manual — per-harness steps below. For anything else, the easiest path is to **ask the agent to do it for you** ("install `qwen-mm-plugins-<cap>`").

Each capability is `qwen-mm-plugins-<cap>` with uvx extras `[<cap>]`; in every block below, replace `<cap>` with a capability name (`core` / `api` / `search` / `video-memory` / `video-edit` / `blender` / `freecad`).

Released plugins use `qwen-mm-plugins-<cap>-v<version>` from
[`plugin-versions.json`](../../plugin-versions.json), never `main`; the installer resolves the
latest version automatically. To install an older release, select only that capability and run:

```bash
QMP_REF=qwen-mm-plugins-search-v1.0.1 bash install.sh install
```

Use [local checkout](#local-checkout) mode for unpublished code.

Claude Code can also install this way — the only difference from the marketplace path is the tool name: marketplace installs carry a plugin prefix + a server key (the capability's own name, e.g. `qwen-mm-plugins-<cap>`), whereas a manual `mcp add` uses the server name you choose. Taking a capability's `read_image` as an example:

- Marketplace: `mcp__plugin_qwen-mm-plugins-<cap>_qwen-mm-plugins-<cap>__read_image`
- Manual: `mcp__qwen-mm-plugins-<cap>__read_image`

### skill link + mcp add (Claude Code and similar)

```bash
# 1) skill
ln -s "$(pwd)/src/capabilities/<cap>/skill" ~/.claude/skills/qwen-mm-plugins-<cap>
# 2) MCP (for local code, replace --from with "$(pwd)[<cap>]")
claude mcp add qwen-mm-plugins-<cap> -- \
  uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" qwen-mm-plugins-<cap>
```

To switch capabilities, replace the skill path, the `[<cap>]` profile, and the entry name `qwen-mm-plugins-<cap>` all together with those of the target capability.

### opencode

`npm i -g opencode-ai`, then register the MCP server under `mcp` in `~/.config/opencode/opencode.json` (or a project `opencode.json`) and drop the skill in `~/.config/opencode/skills/`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "qwen-mm-plugins-<cap>": {
      "type": "local",
      "command": ["uvx", "--from", "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>", "qwen-mm-plugins-<cap>"],
      "environment": { "DASHSCOPE_API_KEY": "{env:DASHSCOPE_API_KEY}" },
      "enabled": true
    }
  }
}
```

```bash
cp -r src/capabilities/<cap>/skill ~/.config/opencode/skills/qwen-mm-plugins-<cap>   # opencode also reads ~/.claude/skills/
```

Headless: `opencode run --auto "…"`. (A custom OpenAI-compatible provider must mark the model image-capable with `modalities`, or opencode drops returned images.)

### Qwen Code

`npm i -g @qwen-code/qwen-code@latest`. Install a capability as a **native extension** (bundles skill + MCP + context) in one command, from the Claude marketplace over git:

```bash
qwen extensions install https://github.com/QwenLM/Qwen-MM-Plugins.git:qwen-mm-plugins-<cap> \
  --ref=qwen-mm-plugins-<cap>-v<version> --consent
```

Or register just the MCP server (then copy the skill into `~/.qwen/skills/qwen-mm-plugins-<cap>`):

```bash
qwen mcp add qwen-mm-plugins-<cap> --scope user --trust --timeout 600000 \
  uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" qwen-mm-plugins-<cap>
```

Headless: `qwen -p "…" --yolo -o text`. Uninstall: `qwen extensions uninstall qwen-mm-plugins-<cap>`.

### Gemini CLI

`npm i -g @google/gemini-cli`. Register the MCP server + install the skill:

```bash
gemini mcp add -s user qwen-mm-plugins-<cap> \
  uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" qwen-mm-plugins-<cap>
# Gemini's skill installer has no --ref; install from a checkout of the same tag.
gemini skills install /path/to/tagged-checkout --path src/capabilities/<cap>/skill --consent
```

From a local checkout, `gemini extensions link src/capabilities/<cap>` bundles both. MCP loads only in **trusted** folders (trust it when prompted). Headless: `gemini -p "…" -y`. Uninstall: `gemini mcp remove -s user qwen-mm-plugins-<cap>` + `gemini skills uninstall qwen-mm-plugins-<cap>`.

> Gemini CLI only talks to the **Google Gemini API** — no external / OpenAI-compatible model providers.

### pi (earendil-works)

`npm i -g @earendil-works/pi-coding-agent`. pi has **native skills** but **no built-in MCP** (by design) — MCP tools come via the community `pi-mcp-adapter` extension:

```bash
cp -r src/capabilities/<cap>/skill ~/.pi/agent/skills/qwen-mm-plugins-<cap>   # skill (native)
pi install npm:pi-mcp-adapter                                               # one-time, for MCP
```

`~/.config/mcp/mcp.json` (same `mcpServers` schema as our `.mcp.json`):

```json
{
  "settings": { "toolPrefix": "none" },
  "mcpServers": { "qwen-mm-plugins-<cap>": {
    "command": "uvx",
    "args": ["--from", "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>", "qwen-mm-plugins-<cap>"],
    "env": { "DASHSCOPE_API_KEY": "${DASHSCOPE_API_KEY}" },
    "directTools": ["read_image", "ocr", "visualize"]
  } }
}
```

Skill-only capabilities (edu-agent) work with just the skill copy. Headless: `pi -p "…"`.

### QwenPaw 2.0

QwenPaw 2.0 has no plugin marketplace of its own, so it can only be installed manually. Using `<cap>` as an example:

```bash
# 1) skill
cp -r src/capabilities/<cap>/skill ~/.qwenpaw/workspaces/default/skills/qwen-mm-plugins-<cap>
qwenpaw skills list      # triggers reconcile, registering it in the manifest (disabled at this point)
qwenpaw skills config    # interactively check to enable
# 2) MCP: add it to mcp.clients in ~/.qwenpaw/workspaces/default/agent.json (no CLI — edit the file directly; hot-reloaded)
```

```json
{
  "mcp": {
    "clients": {
      "qwen-mm-plugins-<cap>": {
        "name": "qwen-mm-plugins-<cap>",
        "enabled": true,
        "transport": "stdio",
        "command": "uvx",
        "args": ["--from", "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>", "qwen-mm-plugins-<cap>"]
      }
    }
  }
}
```

## Dependencies

`uvx` installs the Python dependencies for the chosen profile into an isolated cache on first launch. Only two things are prepared manually.

### API keys (only for API-based tools)

`vision_chat` / `ocr` / `grounding` / `transcribe_audio` / generation tools require `DASHSCOPE_API_KEY`, inherited from the shell environment (or `~/.qwen-mm-plugins/config`). The web tools (`web_search` / `web_extractor` / `image_search`) use the Serper API and require `SERPER_API_KEY` instead. Native image/video/document reading needs no key.

### System tools (install manually with your system package manager)

| Tool | Powers | Install |
|------|-----------|------|
| **ffmpeg** | `read_video` / `transcribe_audio` / video-memory / video-edit | `apt install ffmpeg`  ·  `brew install ffmpeg` |
| **libreoffice** | Office / DrawIO in `visualize` | `apt install libreoffice`  ·  `brew install --cask libreoffice` |
| **blender** | high-quality 3D rendering in `visualize` (optional, falls back to matplotlib by default) | `apt install blender`  ·  `brew install --cask blender` |
| **texlive** (pdflatex) | LaTeX in `visualize` | `apt install texlive-latex-base texlive-latex-extra` |
| **chromium** (playwright) | web-page screenshots in `visualize` | `playwright install chromium` |

How to see which system tools are missing:

- Check with uvx: `uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" qwen-mm-plugins-<cap> --check-system`.
- At server startup, if an installed extra is missing its system tool, a warning line is printed to stderr.
- At actual tool-call time, you get a "please install X" text message, while other tools keep working.

### edu-agent exception: skill-only, deps prepared manually

`qwen-mm-plugins-edu-agent` is a **pure skill** (no MCP server), so "installing a plugin needs no manual pip" does **not** apply — `uvx` installs nothing for it, and its runtime deps must be prepared by hand:

| Dependency | Powers | Install / check |
|------|--------|-----------------|
| **Node.js + npm/npx** (≥18) | scaffold + render (`npx hyperframes`) | `node -v` |
| **hyperframes CLI** | `init` / `lint` / `validate` / `render` | pulled on demand by `npx hyperframes` (needs npm-registry access to scaffold; version pinned in-project afterward) |
| **Headless Chromium + OS libs** | `npx hyperframes render` (puppeteer) + post-render QA gates | auto-downloaded by puppeteer on first `npx hyperframes`; on minimal Linux also `apt install libnss3 libatk-bridge2.0-0 libgbm1 libasound2 libxkbcommon0 libgtk-3-0 fonts-noto-cjk` (else Chrome won't launch / CJK renders as tofu). Reuse a system Chrome via `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium` |
| **Python** `dashscope` `soundfile` `numpy` `requests` | Step-3 TTS synth + stitch | `python3 -m pip install dashscope soundfile numpy requests` |
| **ffmpeg** | loudness normalize (`loudnorm`) + post-render frame self-check | `apt install ffmpeg` · `brew install ffmpeg` |
| **`DASHSCOPE_API_KEY`** | Qwen-TTS (`qwen3-tts-flash`) | inherited from the shell |

> Network boundary: `npx hyperframes init` and the TTS calls need internet; the **render itself is offline** (so fonts / KaTeX / GSAP are self-hosted into `dist/`). Full checklist: the skill's `SKILL.md` → "Prerequisites".

### Environment variables

Config is read from the shell environment, falling back to `~/.qwen-mm-plugins/config` (KEY=VALUE lines, read when a var isn't already in the environment — so GUI-launched harnesses pick it up too). In practice only the two API keys above are commonly needed; everything else is optional. To edit that file, run the installer's **Configure** action or `<entry> --setup` — both now browse & edit the **whole** config grouped by category (credentials, dirs/limits, video-memory, OSS, Blender/FreeCAD hosts, edu-agent), not just the API key. For automation: `<entry> --set KEY=VALUE …` / `<entry> --unset KEY …`.

| Variable | Used by | Default |
|---|---|---|
| `DASHSCOPE_API_KEY` | vision_chat · ocr · grounding · transcribe_audio · generation · video-memory build | *(required for these)* |
| `SERPER_API_KEY` | web_search · web_extractor · image_search | *(required for these)* |
| `DASHSCOPE_BASE_URL` | override the DashScope endpoint | DashScope compat URL |
| `SAM3_SERVER_URL` | `segmentation` (SAM3 server) | *(required for segmentation)* |
| `ASR_SERVER_URLS` | `transcribe_audio` self-hosted fallback (comma-separated, round-robined) when DashScope fails | *unset → DashScope only* |
| `QWEN_MM_FFMPEG_TIMEOUT` | ffmpeg timeout, seconds | `120` |
| `QWEN_MM_MAX_TOTAL_FRAMES` | max frames sampled from a video | `600` |
| `QWEN_MM_CACHE` | cache dir for derived render artifacts | OS cache dir |
| `QWEN_MM_CONFIG_DIR` | override the config dir that GUI harnesses read for keys | `~/.qwen-mm-plugins` |
| `QWEN_MM_CONFIG` | override the full config-file path | `<config dir>/config` |

> **blender / freecad** are thin clients — they connect to a **running** Blender / FreeCAD carrying the bundled addon. `QWEN_MM_AUTOLAUNCH=1` (preset in the plugin manifests) brings the app up on the first tool call, auto-downloading it on Linux-x86_64 if missing. See [`cookbooks/blender`](../../cookbooks/blender/usage.md) / [`cookbooks/freecad`](../../cookbooks/freecad/usage.md) for the full setup, env vars, and troubleshooting.

## Repository layout

```
src/
├── capabilities/            #   one directory per capability (may contain a skill and/or its companion MCP tools)
│   ├── core/                #     vision: read_image / read_video / visualize / ocr / grounding / …
│   ├── video-memory/        #     long-video memory: hierarchical graph + semantic search
│   ├── video-edit/          #     video editing + image/video/audio generation
│   ├── blender/             #     Blender thin client (bundled addon: vendor/ + --launch-app)
│   ├── freecad/             #     FreeCAD thin client (bundled addon: vendor/ + --launch-app)
│   └── example/             #     template: skill + tools
├── shared/                  #   shared library (reusable code: env/content/image/video/cache/syscmd/api_openai/api_dashscope …)
└── mcp_framework.py         #   shared framework (tool auto-registration + FastMCP serve)
pyproject.toml               # the single distribution qwen-mm-plugins (entries / extras / version)
.claude-plugin/  tests/  ruff.toml   # .claude-plugin/marketplace.json = the native plugin marketplace
```
