# Qwen-MM-Plugins

[English](README.md) · **中文**

面向 Qwen 模型的原生多模态理解插件，让任何 Agent Harness 都具备原生多模态能力。

## 目录

- [🧩 能力](#-能力)
- [🏗 架构](#-架构)
- [📦 安装](#-安装)
- [🔧 依赖](#-依赖)
- [🔑 配置](#-配置)
- [🚀 快速开始](#-快速开始)
- [🧪 开发](#-开发)

## 🧩 能力

每个能力单独安装 —— 一个 **skill**（让模型知道有这套工具）+ 一个可选的 **MCP server**（工具本体）。

我们提供了一组 [**cookbooks**](cookbooks/),展示 Qwen3.8-Max + 这些插件的实战效果 —— 每个能力的 cookbook(见下表各行)含完整工具清单、安装与实测案例。

| 能力 | 做什么 | 安装名 | Cookbook |
|---|---|---|---|
| **core** | 本地 I/O 插件：动态分辨率读取图片与视频，可视化任意文件(如文档、3D 等)——外加一些图像工具(裁剪 / 标注 / 抽帧) | `qwen-mm-plugins-core` | [link](cookbooks/core/usage.md) |
| **api** | 云端 API 理解媒体，按模型族划分:VL(视觉对话、OCR、grounding)、Omni 音视频(带时间戳或说话人标签的 ASR / 多说话人分离、分段描述、时序定位、事件计数),外加 ASR 与分割(SAM3);目前支持 DashScope | `qwen-mm-plugins-api` | [link](cookbooks/api/usage.md) |
| **search** | 联网 + 反查图搜索用于事实核验:网页搜索、网页抽取、反查图;目前支持 Serper | `qwen-mm-plugins-search` | [TBD](cookbooks/core/usage.md) |
| **video-memory** | 长视频记忆：层次化图记忆，支撑超长视频问答 | `qwen-mm-plugins-video-memory` | [link](cookbooks/video-memory/usage.md) |
| **video-edit** | 视频剪辑 + 生成：剪辑工作流 + 图片 / 视频 / 音频生成 | `qwen-mm-plugins-video-edit` | — |
| **blender** | Blender 三维建模：对一个**正在运行**的 Blender 写 Python（瘦客户端，22 工具）—— 建模 / 材质 / 灯光 / 渲染 | `qwen-mm-plugins-blender` | [link](cookbooks/blender/usage.md) |
| **freecad** | FreeCAD 参数化 CAD：驱动一个**正在运行**的 FreeCAD（瘦客户端，14 工具）—— 建模、改属性、STEP/STL 导入导出、FEM 分析 | `qwen-mm-plugins-freecad` | [link](cookbooks/freecad/usage.md) |
| **edu-agent** | 讲题视频：把一道数学 / 理科题或题目图片变成一步步讲解的中文视频 / 交互页面（**纯 skill**，无 MCP server） | `qwen-mm-plugins-edu-agent` | — |

## 🏗 架构

![Qwen-MM-Plugins 架构](docs/assets/architecture.svg)

## 📦 安装

一个能力 = 一个 **skill**（让模型知道有这套工具）+ 一个可选的 **MCP server**（工具本体，`uvx` 按需拉起 —— 依赖 [uv](https://docs.astral.sh/uv/)，不用手动 pip）。

### 推荐：引导式安装器

一个脚本搞定 **install · configure · verify · uninstall**，覆盖它支持的所有 harness（Claude Code · Codex · Qoder · OpenClaw · Qwen Code · Gemini CLI）。它底层调各 harness 自己的原生安装 —— 不重造轮子 —— 并把配置写进统一的 `~/.qwen-mm-plugins/config`（GUI / 终端都读），一次配好：

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash   # 引导菜单
```

也可以只跑单个动作 —— `bash install.sh install` / `local` / `configure` / `verify` / `uninstall`。

普通安装会把每个能力分别解析到各自最新的不可变 release tag：更新一个插件不会带着其他
插件移动，正式 MCP 也不再跟随 `main`。当前稳定版本见
[`plugin-versions.json`](plugin-versions.json)；显式回退 / 开发 ref 见[详细安装说明](docs/zh/installation.md)。

安装 checkout 中尚未发布的代码时，直接复用同一套引导流程：

```bash
git clone https://github.com/QwenLM/Qwen-MM-Plugins.git
cd Qwen-MM-Plugins && bash install.sh local
```

**Windows x64：**推荐使用 WSL2（建议 Ubuntu），在 WSL home 目录中 clone 仓库
（例如 `~/code`），不要放在 `/mnt/c` 这类 Windows 挂载盘下，然后运行相同命令。
当前 Windows 仅支持 WSL2；原生 Windows 尚未完成验证。简要说明见
[Windows 安装说明](docs/zh/installation.md#windows-wsl2)。

### 手动（逐 harness）

想用 harness 自己的命令，或你在 opencode / pi / QwenPaw 上（安装器不覆盖这几个）？那就自己注册 skill + MCP。

**有插件市场的 harness**（Claude Code · Qoder · Codex · OpenClaw · Qwen Code）—— 加市场，再装某个能力（把 `<cap>` 换成 `core` / `api` / `search` / `video-memory` / `video-edit` / `blender` / `freecad`）。默认先装 `core`（本地 I/O 基座,其他能力都建立在它之上）,再按需装其他:

```bash
# Claude Code
claude   plugin  marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude   plugin  install       qwen-mm-plugins-<cap>@qwen-mm-plugins
# Qoder
qodercli plugins marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
qodercli plugins install       qwen-mm-plugins-<cap>@qwen-mm-plugins
# Codex
codex    plugin  marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
codex    plugin  add           qwen-mm-plugins-<cap>@qwen-mm-plugins
# OpenClaw（远程 marketplace 不接受 git-subdir entry，需使用本地 checkout）
mkdir -p ~/.qwen-mm-plugins
test -d ~/.qwen-mm-plugins/openclaw-marketplace/.git || \
  git clone https://github.com/QwenLM/Qwen-MM-Plugins.git ~/.qwen-mm-plugins/openclaw-marketplace
git -C ~/.qwen-mm-plugins/openclaw-marketplace pull --ff-only
openclaw plugins install qwen-mm-plugins-<cap> \
  --marketplace ~/.qwen-mm-plugins/openclaw-marketplace
# Qwen Code
qwen extensions install https://github.com/QwenLM/Qwen-MM-Plugins.git:qwen-mm-plugins-<cap> \
  --ref=qwen-mm-plugins-<cap>-v<version> --consent
```

引导式安装器会自动维护 OpenClaw 所需的本地 catalog。本地开发时，`bash install.sh local`
负责完整 harness 流程；`scripts/dev-plugin.sh <cap>` 是只处理单个能力的底层助手。在
**codex** 上，更新已有远程 catalog 后应先执行 `plugin marketplace upgrade` 再 `plugin add`。

**其它 harness**（Gemini CLI · opencode · pi · QwenPaw · …）在各自配置里注册 skill + MCP —— 各 harness 的精确配置块见 [`docs/zh/installation.md`](docs/zh/installation.md)。最省事：**直接让 agent 帮你装** —— 「装一下 `qwen-mm-plugins-<cap>`」。

## 🔧 依赖

`uvx` 首次启动时按 profile 把 Python 依赖装进隔离缓存 —— 不用手动 pip。需要你自己装的只有**系统工具**：`ffmpeg`（视频 / 音频），外加 `visualize` 可选的 `libreoffice` / `blender` / `texlive` / `chromium`。跑 `bash install.sh verify` 自检已装的能力 —— 它会确认 API key、并报告缺哪些系统工具（内部对每个能力预拉起 uvx 环境并跑 `--check-system`）。完整系统工具表、edu-agent（纯 skill）的准备、以及 blender/freecad 瘦客户端说明，见 [`docs/zh/installation.md`](docs/zh/installation.md)。

## 🔑 配置

API 类工具需要 key —— 原生读图 / 视频 / 文档不需要：

- `DASHSCOPE_API_KEY` —— `vision_chat` / `ocr` / `grounding` / `transcribe_audio` / Omni 音视频理解 / 生成类 / video-memory 构建
- `SERPER_API_KEY` —— `web_search` / `web_extractor` / `image_search`

在 shell 里 export，或写进 `~/.qwen-mm-plugins/config`（仅当变量未在环境中时才读取 —— 这样 GUI 启动的 harness 也能拿到）。引导式安装器的 Configure 步骤会帮你写这个文件：

```bash
bash install.sh configure     # 交互式：API key、端点、目录、OSS、主机地址 —— 整份分组配置
```

非交互 / 自动化配置与完整环境变量表见 [`docs/zh/installation.md`](docs/zh/installation.md)。

## 🚀 快速开始

装好某个能力后，在 harness 里引用文件直接提问，模型会自动调用对应工具。读取是**动态分辨率**的：每张图片、每帧视频、每页文档都会自动缩放到 VL 模型的 patch grid —— 一张 4K 截图上的细小文字和一张小缩略图都能以各自需要的清晰度读进来，无需手动缩放。

```text
# core —— 读图片 / 视频 / 文档 / 3D 模型（本地、动态分辨率）
@dashboard-4k.png      读出这张仪表盘里的每一个数字。
@report.pdf            总结第 3 页。

# api —— 云端 VL + Omni API：caption / OCR / grounding / 分割 / ASR,外加 Omni 音视频理解
@receipt.jpg           OCR 这张小票并把各行金额加总。
@street.jpg            把画面里每一辆车都框出来。                    # grounding
@meeting.mp4           带说话人标签和时间戳转写这段会议。            # omni
@sports-clip.mp4       统计每次成功传球，并列出发生时间。            # omni
@song.mp3              标注曲风、情绪、乐器、调性和人声特征。        # omni

# search —— 联网 + 反查图，核实画面里的东西
@place.jpg             这张照片是在哪拍的？                     # image_search + web_search

# video-memory —— 对长视频提问；首次提问自动构建 memory
@lecture-2h.mp4        这段视频的主要观点是什么？带上时间戳。

# video-edit —— 图片 / 视频 / 音频生成 + 剪辑工作流
                       生成一张 1024×1024 的图：小熊猫在深夜敲代码。
@/path/to/media        帮我把这个视频剪到大约 3 分钟。

# blender —— 驱动正在运行的 Blender 建模 / 材质 / 灯光 / 渲染（瘦客户端，22 工具）
                       建一个低多边形木凳，加一盏暖色主光，然后渲染出来。

# freecad —— 在正在运行的 FreeCAD 里做参数化 CAD（瘦客户端，14 工具；STEP/STL、FEM）
                       建一颗 M6 六角螺栓、长 30 mm，导出成 STEP。

# edu-agent —— 把一道数学 / 理科题变成一步步讲解的中文视频（纯 skill）
@geometry-problem.png  把这道题的解法讲清楚，做成带旁白的视频。
```

每个能力的全部工具、安装与实测案例见其 🍳 [cookbook](cookbooks/)。

## 🧪 开发

开发环境、贡献规范和检查命令见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。详细指南：
[本地调试](docs/zh/local_development.md) · [添加能力](docs/zh/how_to_add_new_capability.md) ·
[测试](docs/zh/testing.md) · [发布插件](docs/zh/releasing.md)。

## 📄 License

Apache-2.0 —— 见 [`LICENSE`](LICENSE)。Blender 和 FreeCAD 能力内置(vendor)了第三方 MIT 许可的代码,署名见 [`src/capabilities/blender/NOTICE.md`](src/capabilities/blender/NOTICE.md) 和 [`src/capabilities/freecad/NOTICE.md`](src/capabilities/freecad/NOTICE.md)。
