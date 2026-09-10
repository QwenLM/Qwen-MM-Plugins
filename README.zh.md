# Qwen-MM-Plugins

[English](README.md) · **中文**

面向 Qwen 模型的原生多模态理解插件，让任何 Agent Harness 都具备原生多模态能力。

[浏览 Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/) ·
[安装指南（英文）](https://qwenlm.github.io/qwen-mm-plugins-hub/docs/) ·
[添加插件（英文）](https://qwenlm.github.io/qwen-mm-plugins-hub/docs/how-to-add-new-capability/)

按能力查找插件，预览 Skill 和工具定义，并在 Cookbook 中直接查看示例视频和交互案例。
Hub 同时收录英文文档；中文文档继续在本仓库维护。

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
| `core` | 读取本地图片和视频帧，可视化文档、代码、数据、3D 模型与 NIfTI 影像，供 agent 查看。提供媒体元数据、裁图、边框标注及页面/视频帧导出。默认原生模式无需 API key。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/core/cookbook/) |
| `nifti` | 查看 NIfTI 体数据，支持自定义源体素轴切片、体级强度归一化、显式窗预设及实际配置回显。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/nifti/cookbook/) |
| `api` | 调用模型服务理解图片、视频和音频：VL 视觉问答/OCR/目标定位，Omni 转写/说话人区分/内容描述/事件分析，以及专用 ASR 和 SAM3 分割。按模型类别配置 DashScope 或兼容的自托管服务。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/api/cookbook/) |
| `search` | 面向任意模型。网页搜索和页面抽取支持 Serper、Exa、Tavily 或 Serply；反向图像搜索使用 Serper。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/search/cookbook/) |

**Qwen VL 系列模型**（例如 **Qwen3.8-Max**、**Qwen3.7-Plus**）：

| 能力 | 用途 | Cookbook |
|---|---|---|
| `video-memory` | 为长视频构建层次化记忆，之后的提问直接从记忆里回答，不必重看视频。需要 DashScope key 和 ffmpeg。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/video-memory/cookbook/) |
| `video-edit` | 生成图片、视频和音频，并在其上运行剪辑工作流。需要 DashScope key、ffmpeg 和 Node。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/video-edit/cookbook/) |
| `blender` | 驱动一个正在运行的 Blender：建模、材质、灯光与渲染。需要已安装 Blender。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/blender/cookbook/) |
| `freecad` | 驱动一个正在运行的 FreeCAD：参数化 CAD、STEP/STL 与 FEM。需要已安装 FreeCAD。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/freecad/cookbook/) |
| `edu-agent` | 生成中文数理讲解视频与交互页面。纯 Skill，需要 Node 和 ffmpeg。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/edu-agent/cookbook/) |

**Qwen Omni 系列模型**（例如 **Qwen3.5-Omni-Plus**）：

> 目前大多数 harness 还不支持把音频原生输入给主模型，因此音频暂时通过 API 处理。

| 能力 | 用途 | Cookbook |
|---|---|---|
| `omni-memory` | 为长音视频构建音视频记忆：谁在场、谁说了什么、怎么说的、听起来是什么样。由 Omni 模型连同音轨一起读取视频。需要 DashScope key 和 ffmpeg。 | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/omni-memory/cookbook/) |

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

需要自定义 NIfTI 查看方式时，使用独立 `nifti` 插件的 `nifti_visualize` 工具：
默认沿源体素轴 2 选取三张内部切片，共用该体数据的 P1–P99 强度范围。
Core 的基础 NIfTI 预览仍保留原有的正交中心切片和逐切片归一化默认行为。

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
- [添加新插件](docs/zh/how_to_add_new_capability.md) · [Hub 维护](docs/zh/hub.md) · [测试](docs/zh/testing.md)

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。Blender 与 FreeCAD 集成的第三方署名分别见
[Blender NOTICE](src/capabilities/blender/NOTICE.md) 和 [FreeCAD NOTICE](src/capabilities/freecad/NOTICE.md)。
