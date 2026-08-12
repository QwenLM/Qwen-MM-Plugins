# Qwen-MM-Plugins

[English](README.md) · **中文**

面向 Qwen 模型的原生多模态理解插件，让任何 Agent Harness 都具备原生多模态能力。

## 架构

![Qwen-MM-Plugins 架构](docs/assets/architecture.svg)

## 安装

引导式安装器支持 Claude Code、Codex、Qoder、OpenClaw、Qwen Code 和 Gemini CLI。它调用各
harness 的原生安装命令，并通过 `~/.qwen-mm-plugins/config` 共享配置。

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

每个能力独立安装，由一个 **Skill** 和可选的 **MCP server** 组成；安装名统一为
`qwen-mm-plugins-<capability>`。

| 能力 | 用途 | 主要依赖 | Cookbook |
|---|---|---|---|
| `core` | 读取图片和视频；可视化文档、代码、数据、3D 文件等 | 无需 API key；音视频需要 ffmpeg；其他格式按需安装应用 | [Cookbook](cookbooks/core/usage.md) |
| `api` | Qwen VL/Omni 视觉理解、OCR、grounding、ASR、分割与音视频理解 | DashScope；本地音视频需要 ffmpeg | [Cookbook](cookbooks/api/usage.md) |
| `search` | 网页搜索、页面抽取和反向图像搜索 | Serper API key | [Cookbook](cookbooks/search/usage.md) |
| `video-memory` | 为长视频问答构建层次化记忆 | DashScope；构建需要 ffmpeg/ffprobe | [Cookbook](cookbooks/video-memory/usage.md) |
| `video-edit` | 图片、视频、音频生成与剪辑工作流 | DashScope；完整剪辑需要 ffmpeg、Node/Chromium | [Cookbook](cookbooks/video-edit/usage.md) |
| `blender` | 在 Blender 中完成建模、材质、灯光与渲染 | Blender；无界面 Linux 需要 Xvfb | [Cookbook](cookbooks/blender/usage.md) |
| `freecad` | 参数化 CAD、STEP/STL 与 FEM 工作流 | FreeCAD；FEM 需要 CalculiX；无界面 Linux 需要 Xvfb | [Cookbook](cookbooks/freecad/usage.md) |
| `edu-agent` | 生成中文数理讲解视频与交互页面 | 纯 Skill；Node/Chromium、ffmpeg；视频旁白需要 DashScope | [Cookbook](cookbooks/edu-agent/usage.md) |

## 快速体验

安装能力后，引用文件并直接提问即可；Skill 会选择对应的 MCP 工具。

```text
@report.pdf          总结第 3 页，并提取其中的表格。
@meeting.mp4         带说话人标签和时间戳转写这段会议。
@place.jpg           判断照片拍摄地点，并联网核实。
@lecture-2h.mp4      按时间戳列出这段长视频的主要观点。
```

`core` 会以动态分辨率读取媒体，通常无需手动缩放。

## 依赖与配置

- [`uv`](https://docs.astral.sh/uv/) 提供 `uvx`，按需安装 Python 依赖。
- 本地 `core` 工具无需 API key；云端和搜索能力需要对应服务的凭证。
- 视频、文档、浏览器、Blender 和 FreeCAD 工作流可能需要系统程序。

通过安装器的 **Configure** 和 **Verify** 操作设置凭证并检查依赖。完整说明见
[安装文档](docs/zh/installation.md#依赖)。

## 文档

- [安装](docs/zh/installation.md)
- [贡献指南](CONTRIBUTING.md) · [本地开发](docs/zh/local_development.md)
- [添加能力](docs/zh/how_to_add_new_capability.md) · [测试](docs/zh/testing.md)

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。Blender 与 FreeCAD 集成的第三方署名分别见
[Blender NOTICE](src/capabilities/blender/NOTICE.md) 和 [FreeCAD NOTICE](src/capabilities/freecad/NOTICE.md)。
