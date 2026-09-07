# Qwen-MM-Plugins

[English](README.md) · **中文**

面向 Qwen 模型的原生多模态理解插件，让任何 Agent Harness 都具备原生多模态能力。

## 架构

![Qwen-MM-Plugins 架构](docs/assets/architecture.svg)

## 安装

引导式安装器支持 Claude Code、CodeBuddy、Codex、Qoder、OpenClaw、Qwen Code 和 Gemini CLI。
共享配置位于 `~/.qwen-mm-plugins/config`。

WorkBuddy、QoderWork 与 QwenWork 的应用内安装，以及 DeepSeek Harness、Hermes Agent、
opencode、pi 和 QwenPaw 的手动安装方式见[其他 Harness 安装](docs/zh/manual_harnesses.md)。

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash
```

更新某个 harness 中已安装的能力：

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash -s -- update
```

正式能力使用彼此独立且不可变的发布 tag。本地 checkout、版本回退、手动 skill + MCP 安装、
依赖以及 Windows/WSL2 说明见[安装文档](docs/zh/installation.md)。

## 能力

每个能力独立安装，由一个 **Skill** 和可选的 **MCP server** 组成，安装名为
`qwen-mm-plugins-<capability>`。按你 agent 的主模型来选。多模态模型强烈建议使用 `core`：
它让主模型原生读取图片、视频和文件，而不是绕到另一个 API、或拼一堆临时的 shell 命令去处理。

**通用**：

| 能力 | 用途 | Cookbook |
|---|---|---|
| `core` | 面向 VL / Omni agentic 模型。原生读取图片和视频，并可视化文档、代码、数据、3D 文件与 NIfTI 影像。全部在本机完成，不需要 API key。 | [Cookbook](cookbooks/core/usage.md) |
| `api` | 面向任意模型（含纯文本模型）。用 DashScope key 或本地 endpoint 调用多模态模型 API：VL 的 `vision_chat` / `ocr` / `grounding`，Omni 的 `omni_av_*` / `omni_asr*` / `omni_music_caption`，另有 `transcribe_audio` 与 `segmentation`。 | [Cookbook](cookbooks/api/usage.md) |
| `search` | 面向任意模型。网页搜索、页面抽取和反向图像搜索，需要 Serper、Exa 或 Tavily key。 | [Cookbook](cookbooks/search/usage.md) |
| `mhs` | 面向任意模型。通过 Model Hardware Standard adapter 操作真实硬件——摄像头、传感器、灯、机械臂、实验设备；主机侧强制执行安全限位，并提供紧急停止。adapter 由硬件方自己运行，不需要云端 key。 | [Cookbook](cookbooks/mhs/usage.md) |

**Qwen VL 系列模型**（例如 **Qwen3.8-Max**、**Qwen3.7-Plus**）：

| 能力 | 用途 | Cookbook |
|---|---|---|
| `video-memory` | 为长视频构建层次化记忆，之后的提问直接从记忆里回答，不必重看视频。需要 DashScope key 和 ffmpeg。 | [Cookbook](cookbooks/video-memory/usage.md) |
| `video-edit` | 生成图片、视频和音频，并在其上运行剪辑工作流。需要 DashScope key、ffmpeg 和 Node。 | [Cookbook](cookbooks/video-edit/usage.md) |
| `blender` | 驱动一个正在运行的 Blender：建模、材质、灯光与渲染。需要已安装 Blender。 | [Cookbook](cookbooks/blender/usage.md) |
| `freecad` | 驱动一个正在运行的 FreeCAD：参数化 CAD、STEP/STL 与 FEM。需要已安装 FreeCAD。 | [Cookbook](cookbooks/freecad/usage.md) |
| `edu-agent` | 生成中文数理讲解视频与交互页面。纯 Skill，需要 Node 和 ffmpeg。 | [Cookbook](cookbooks/edu-agent/usage.md) |

**Qwen Omni 系列模型**（例如 **Qwen3.5-Omni-Plus**）：

> 目前大多数 harness 还不支持把音频原生输入给主模型，因此音频暂时通过 API 处理。

| 能力 | 用途 | Cookbook |
|---|---|---|
| `omni-memory` | 为长音视频构建音视频记忆：谁在场、谁说了什么、怎么说的、听起来是什么样。由 Omni 模型连同音轨一起读取视频。需要 DashScope key 和 ffmpeg。 | [Cookbook](cookbooks/omni-memory/usage.md) |

具体版本与可选依赖见[安装文档](docs/zh/installation.md#依赖)。

## 快速体验

安装能力后，引用文件并直接提问即可；Skill 会选择对应的 MCP 工具。

```text
@report.pdf          总结第 3 页，并提取其中的表格。
@meeting.mp4         带说话人标签和时间戳转写这段会议。
@place.jpg           判断照片拍摄地点，并联网核实。
@lecture-2h.mp4      按时间戳列出这段长视频的主要观点。
@brain.nii.gz        查看元数据和三个正交方向的中心切片。
```

`core` 会以动态分辨率读取媒体，通常无需手动缩放。
NIfTI 文件仅在本地以只读方式打开，不会上传；该可视化能力不用于临床诊断。

## 依赖与配置

- [`uv`](https://docs.astral.sh/uv/) 提供 `uvx`，按需安装 Python 依赖。
- 本地 `core` 工具在默认原生图片模式下无需 API key；纯文本图片描述 fallback、云端和搜索能力
  需要对应服务的凭证。
- 视频、文档、浏览器、Blender 和 FreeCAD 工作流可能需要系统程序。

通过安装器的 **Configure** 和 **Verify** 操作设置凭证并检查依赖。系统要求见
[安装文档](docs/zh/installation.md#依赖)，全部设置见[配置参考（英文）](docs/en/configuration.md)。

## 文档

- [安装](docs/zh/installation.md)
- [配置参考（英文）](docs/en/configuration.md)
- [贡献指南](CONTRIBUTING.md) · [本地开发](docs/zh/local_development.md)
- [添加能力](docs/zh/how_to_add_new_capability.md) · [测试](docs/zh/testing.md)

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。Blender 与 FreeCAD 集成的第三方署名分别见
[Blender NOTICE](src/capabilities/blender/NOTICE.md) 和 [FreeCAD NOTICE](src/capabilities/freecad/NOTICE.md)。
