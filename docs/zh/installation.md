# 安装（详细）

快速路径 —— 插件市场与引导式安装 —— 在 [README](../../README.zh.md#-安装)。本页覆盖**不支持插件直装的 harness**（手动 skill + MCP 安装）、由此产生的工具名前缀、完整依赖参考，以及目录结构。

## Windows（WSL2）

Windows x64 用户推荐安装 Ubuntu WSL2，并在 WSL home 目录中 clone 仓库（例如
`~/code`），不要放在 `/mnt/c` 这类 Windows 挂载盘下。然后按照 Linux/macOS 的
相同命令安装。在管理员 PowerShell 中运行：

```powershell
wsl --install -d Ubuntu
```

使用 Windows 版 Codex 时，请将 agent 环境设为 WSL2，重启 Codex，并在该 WSL
环境中安装和使用插件。当前 Windows 仅支持 WSL2；原生 Windows 尚未完成验证。

## 非市场 harness：直接注册 skill + MCP

没有插件市场的 harness 需要在各自的配置里注册 **skill** 和 **MCP server**。**Qwen Code** 和 **Gemini CLI** 已被[引导式安装器](../../README.zh.md#-安装)自动化（`bash install.sh` → 选对应 harness）；其余（opencode、pi、QwenPaw 等）为手动 —— 见下方各 harness 步骤。其它 harness 最省事的办法是**让 agent 帮你装**（「装一下 `qwen-mm-plugins-<cap>`」）。

每个能力都是 `qwen-mm-plugins-<cap>`、uvx extras 为 `[<cap>]`；下面每个块里把 `<cap>` 换成具体能力名（`core` / `video-memory` / `video-edit` / `blender` / `freecad`）。

Claude Code 也可以走手动安装 —— 和插件市场的区别只在工具名：插件市场装的带 plugin 前缀 + server key（就是能力自己的名字，例如 `qwen-mm-plugins-<cap>`），手动 `mcp add` 用你自定义的 server name。以某个能力的 `read_image` 为例：

- 插件市场：`mcp__plugin_qwen-mm-plugins-<cap>_qwen-mm-plugins-<cap>__read_image`
- 手动：`mcp__qwen-mm-plugins-<cap>__read_image`

### skill link + mcp add（Claude Code 及同类）

```bash
# 1) skill
ln -s "$(pwd)/src/capabilities/<cap>/skill" ~/.claude/skills/qwen-mm-plugins-<cap>
# 2) MCP（本地代码把 --from 换成 "$(pwd)[<cap>]"）
claude mcp add qwen-mm-plugins-<cap> -- \
  uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main" qwen-mm-plugins-<cap>
```

换能力时，把 skill 路径、`[<cap>]` profile、入口名 `qwen-mm-plugins-<cap>` 一起换成对应能力的。

### opencode

`npm i -g opencode-ai`，然后在 `~/.config/opencode/opencode.json`（或项目级 `opencode.json`）的 `mcp` 下注册 MCP server，并把 skill 放进 `~/.config/opencode/skills/`：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "qwen-mm-plugins-<cap>": {
      "type": "local",
      "command": ["uvx", "--from", "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main", "qwen-mm-plugins-<cap>"],
      "environment": { "DASHSCOPE_API_KEY": "{env:DASHSCOPE_API_KEY}" },
      "enabled": true
    }
  }
}
```

```bash
cp -r src/capabilities/<cap>/skill ~/.config/opencode/skills/qwen-mm-plugins-<cap>   # opencode 也会读 ~/.claude/skills/
```

无头运行：`opencode run --auto "…"`。（自定义 OpenAI 兼容 provider 必须用 `modalities` 把模型标为可读图，否则 opencode 会丢掉返回的图片。）

### Qwen Code

`npm i -g @qwen-code/qwen-code@latest`。把一个能力作为**原生 extension** 安装（打包 skill + MCP + context），一条命令、走 git 从 Claude 市场拉：

```bash
qwen extensions install https://github.com/QwenLM/Qwen-MM-Plugins.git:qwen-mm-plugins-<cap> --consent
```

或只注册 MCP server（再把 skill 拷进 `~/.qwen/skills/qwen-mm-plugins-<cap>`）：

```bash
qwen mcp add qwen-mm-plugins-<cap> --scope user --trust --timeout 600000 \
  uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main" qwen-mm-plugins-<cap>
```

无头运行：`qwen -p "…" --yolo -o text`。卸载：`qwen extensions uninstall qwen-mm-plugins-<cap>`。

### Gemini CLI

`npm i -g @google/gemini-cli`。注册 MCP server + 安装 skill：

```bash
gemini mcp add -s user qwen-mm-plugins-<cap> \
  uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main" qwen-mm-plugins-<cap>
gemini skills install https://github.com/QwenLM/Qwen-MM-Plugins.git --path src/capabilities/<cap>/skill --consent
```

本地 checkout 时，`gemini extensions link src/capabilities/<cap>` 可同时打包两者。MCP 只在**受信任目录**下加载（提示时信任该目录即可）。无头运行：`gemini -p "…" -y`。卸载：`gemini mcp remove -s user qwen-mm-plugins-<cap>` + `gemini skills uninstall qwen-mm-plugins-<cap>`。

> Gemini CLI 只对接 **Google Gemini API** —— 不支持外部 / OpenAI 兼容的模型 provider。

### pi (earendil-works)

`npm i -g @earendil-works/pi-coding-agent`。pi 有**原生 skills**，但**没有内置 MCP**（设计如此）—— MCP 工具通过社区扩展 `pi-mcp-adapter` 提供：

```bash
cp -r src/capabilities/<cap>/skill ~/.pi/agent/skills/qwen-mm-plugins-<cap>   # skill（原生）
pi install npm:pi-mcp-adapter                                               # 一次性，用于 MCP
```

`~/.config/mcp/mcp.json`（`mcpServers` schema 与我们的 `.mcp.json` 相同）：

```json
{
  "settings": { "toolPrefix": "none" },
  "mcpServers": { "qwen-mm-plugins-<cap>": {
    "command": "uvx",
    "args": ["--from", "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main", "qwen-mm-plugins-<cap>"],
    "env": { "DASHSCOPE_API_KEY": "${DASHSCOPE_API_KEY}" },
    "directTools": ["read_image", "ocr", "visualize"]
  } }
}
```

纯 skill 能力（edu-agent）只拷 skill 即可用。无头运行：`pi -p "…"`。

### QwenPaw 2.0

QwenPaw 2.0 本身不支持插件市场，所以只能手动安装，以 `<cap>` 为例：

```bash
# 1) skill
cp -r src/capabilities/<cap>/skill ~/.qwenpaw/workspaces/default/skills/qwen-mm-plugins-<cap>
qwenpaw skills list      # 触发 reconcile，登记进 manifest（此时 disabled）
qwenpaw skills config    # 交互勾选启用
# 2) MCP：写进 ~/.qwenpaw/workspaces/default/agent.json 的 mcp.clients（无 CLI，改文件即可，有热加载）
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
        "args": ["--from", "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main", "qwen-mm-plugins-<cap>"]
      }
    }
  }
}
```

## 依赖

`uvx` 首次启动时按 profile 把 Python 依赖装进隔离缓存。需要手动准备的只有两类。

### API Key（仅 API 工具需要）

`vision_chat` / `ocr` / `grounding` / `transcribe_audio` / 生成类工具需 `DASHSCOPE_API_KEY`，从 shell 环境继承（或写进 `~/.qwen-mm-plugins/config`）。联网工具（`web_search` / `web_extractor` / `image_search`）走 Serper API，需要 `SERPER_API_KEY`。原生读图 / 视频 / 文档不需要 key。

### 系统工具（需用系统包管理器手动装）

| 工具 | 供哪些功能 | 安装 |
|------|-----------|------|
| **ffmpeg** | `read_video` / `transcribe_audio` / video-memory / video-edit | `apt install ffmpeg`  ·  `brew install ffmpeg` |
| **libreoffice** | `visualize` 的 Office / DrawIO | `apt install libreoffice`  ·  `brew install --cask libreoffice` |
| **blender** | `visualize` 的 3D 高质量渲染（可选，默认回退 matplotlib） | `apt install blender`  ·  `brew install --cask blender` |
| **texlive**（pdflatex） | `visualize` 的 LaTeX | `apt install texlive-latex-base texlive-latex-extra` |
| **chromium**（playwright） | `visualize` 的网页截图 | `playwright install chromium` |

如何查看缺少的系统工具：

- 用 uvx 查看：`uvx --from "qwen-mm-plugins[all] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main" qwen-mm-plugins-<cap> --check-system`。
- server 启动时，若某个已装 extra 缺对应系统工具，会在 stderr 打印一行告警。
- 实际工具调用时，返回「请安装 X」的文字提示，其它工具照常用。

### edu-agent 例外：纯 skill，依赖需手动准备

`qwen-mm-plugins-edu-agent` 是**纯 skill**（没有 MCP server），所以「装插件不用手动 pip」对它**不适用** —— `uvx` 不会替它装任何东西，运行时依赖要自己备齐：

| 依赖 | 供哪些功能 | 安装 / 检查 |
|------|-----------|-------------|
| **Node.js + npm/npx**（≥18） | 脚手架 + 渲染（`npx hyperframes`） | `node -v` |
| **hyperframes CLI** | `init` / `lint` / `validate` / `render` | `npx hyperframes` 按需拉取（脚手架时需能访问 npm registry，之后项目内 pin 版本） |
| **Headless Chromium + 系统库** | `npx hyperframes render`（puppeteer）+ 渲染后 QA 门禁 | puppeteer 首次 `npx hyperframes` 时自动下载；最小化 Linux 上还需 `apt install libnss3 libatk-bridge2.0-0 libgbm1 libasound2 libxkbcommon0 libgtk-3-0 fonts-noto-cjk`（否则 Chrome 起不来 / 中文显示成豆腐块）。可用 `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium` 复用系统 Chrome |
| **Python 包** `dashscope` `soundfile` `numpy` `requests` | Step 3 TTS 合成与拼接 | `python3 -m pip install dashscope soundfile numpy requests` |
| **ffmpeg** | 响度归一化（`loudnorm`）+ 渲染后抽帧自查 | `apt install ffmpeg` · `brew install ffmpeg` |
| **`DASHSCOPE_API_KEY`** | Qwen-TTS（`qwen3-tts-flash`） | 从 shell 继承 |

> 网络边界：`npx hyperframes init` 和 TTS 调用需联网；**渲染本身是离线的**（所以字体 / KaTeX / GSAP 要自托管进 `dist/`）。完整清单见该 skill 的 `SKILL.md` → "Prerequisites（环境准备）"。

### 环境变量

配置从 shell 环境读取，其次回退到 `~/.qwen-mm-plugins/config`（每行一个 KEY=VALUE，仅当变量未在环境中时才读取——这样 GUI 启动的 harness 也能拿到）。实际上通常只需要上面那两个 API Key，其余都可选。要编辑该文件，运行安装器的 **Configure** 项或 `<entry> --setup`——两者现在都会按分类（凭据、目录/上限、video-memory、OSS、Blender/FreeCAD 主机、edu-agent）浏览并编辑**整份**配置，而不再只是 API Key。自动化场景用 `<entry> --set KEY=VALUE …` / `<entry> --unset KEY …`。

| 变量 | 用于 | 默认 |
|---|---|---|
| `DASHSCOPE_API_KEY` | vision_chat · ocr · grounding · transcribe_audio · 生成 · video-memory 构建 | *(这些功能必填)* |
| `SERPER_API_KEY` | web_search · web_extractor · image_search | *(这些功能必填)* |
| `DASHSCOPE_BASE_URL` | 覆盖 DashScope 端点 | DashScope 兼容地址 |
| `SAM3_SERVER_URL` | `segmentation`（SAM3 服务） | *(分割必填)* |
| `ASR_SERVER_URLS` | `transcribe_audio` 自建兜底（逗号分隔、轮询），DashScope 失败时用 | *未设 → 仅用 DashScope* |
| `QWEN_MM_FFMPEG_TIMEOUT` | ffmpeg 超时（秒） | `120` |
| `QWEN_MM_MAX_TOTAL_FRAMES` | 单个视频最多抽帧数 | `600` |
| `QWEN_MM_MAX_RESPONSE_BYTES` | 工具响应体上限 | `15 MiB` |
| `QWEN_MM_STREAM_THRESHOLD` | 超过此大小的输出走流式 | `1 MiB` |
| `QWEN_MM_CACHE` | 渲染派生产物的缓存目录 | 系统缓存目录 |
| `QWEN_MM_CONFIG_DIR` | 覆盖 GUI harness 读取密钥的配置目录 | `~/.qwen-mm-plugins` |
| `QWEN_MM_CONFIG` | 覆盖配置文件的完整路径 | `<配置目录>/config` |

> **blender / freecad** 是瘦客户端 —— 它们连接到一台**正在运行**、装好随包 addon 的 Blender / FreeCAD。`QWEN_MM_AUTOLAUNCH=1`（插件清单里默认预设）会在第一次工具调用时把应用拉起来，Linux-x86_64 上缺应用时自动下载。完整安装、环境变量与排障见 [`cookbooks/blender`](../../cookbooks/blender/usage.md) / [`cookbooks/freecad`](../../cookbooks/freecad/usage.md)。

## 沙盒化 GUI harness 的网络限制（Codex 桌面 / Claude / Qoder 等）

当你用 **Codex 桌面**（macOS / Windows 沙盒化的 Electron 应用）、Claude Desktop、Qoder 或任何把 stdio MCP server 当作受限子进程拉起的 GUI 客户端时，可能遇到这个症状：

- `codex mcp list`（或对应 harness 的 mcp 列表命令）里所有 `qwen-mm-plugins-*` server 状态都是 `enabled`
- 工具在 LLM 端能看到，**但每次调用都返回 `unsupported call`**
- 单独在 shell 里跑 `uvx --from "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main" qwen-mm-plugins-<cap>` 是正常的

根因是：harness 的 Electron 沙盒在 spawn stdio 子进程时**禁止访问公网**（典型表现是 `codex plugin marketplace upgrade qwen-mm-plugins` 也报 `Could not resolve host: github.com`）。子进程 `uvx` 第一次启动需要从 `git+https://github.com/...` 拉包 → DNS 解析失败 → 进程静默退出 → harness 把这次调用回退成 "unsupported call"。

修复：把 `.mcp.json` 里的 `git+https://...` 改写为 `file://<本地 checkout>`，让 `uvx` 直接从本地装，不再访问 GitHub。`install.sh` 已经把这个流程封装成 `localize` 子命令：

```bash
# 1) 拿一份本地 checkout（任何方式都行：git clone、Codex marketplace、curl|bash）
git clone https://github.com/QwenLM/Qwen-MM-Plugins.git ~/qwen-mm-plugins
cd ~/qwen-mm-plugins

# 2) 跑 install.sh localize —— 默认就用本仓库的 src/ 目录
bash install.sh localize
# 或显式指定一个不同的 checkout：
# QMP_REPO=file:///path/to/another/checkout/src bash install.sh localize

# 3) **完全退出并重启你的 GUI harness**（Codex 桌面、Claude、Qoder 等）
# 每个新会话首次调用才 spawn stdio 子进程 —— 已经打开的会话仍会用旧 URL。
```

幂等：再跑一次 `localize` 会显示 `unchanged 6 (already on file://)`，不会重复改写。`git pull` 之后再跑一次即可让本地路径指向最新代码（不需要重新 `plugin install`）。

回滚：手动把每个 `src/capabilities/<cap>/.mcp.json` 里的 `file://...` 改回 `qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main`，或者 `git checkout -- src/capabilities/*/.mcp.json`。

> **为什么 `install.sh install` 之后还要再跑一次 `localize`？** `install` 只调用 harness 的 `plugin install` 流程，那个流程直接读仓库里写死的 `.mcp.json`（默认是 `git+https://...`）；要让 stdio 子进程用本地路径，必须额外跑 `localize` 改写 `.mcp.json`。改完请重启 GUI harness 才会生效。

## 目录结构

```
src/
├── capabilities/            #   每个能力一个目录（可包含 skill 或配套的 mcp tools）
│   ├── core/                #     vision：read_image / read_video / visualize / ocr / grounding / …
│   ├── video-memory/        #     长视频记忆：层次图 + 语义检索
│   ├── video-edit/          #     视频剪辑 + 图片/视频/音频生成
│   ├── blender/             #     Blender 瘦客户端（随包 addon：vendor/ + --launch-app）
│   ├── freecad/             #     FreeCAD 瘦客户端（随包 addon：vendor/ + --launch-app）
│   └── example/             #     模板：skill + tools
├── shared/                  #   共享库（env/content/image/video/cache/syscmd/api_openai/api_dashscope 等可复用代码）
└── mcp_framework.py         #   共享框架（工具自动注册 + FastMCP serve）
pyproject.toml               # 唯一分发包 qwen-mm-plugins（入口 / extras / 版本）
.claude-plugin/  tests/  ruff.toml   # .claude-plugin/marketplace.json = 原生插件市场
```
