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

每个能力独立安装，由一个 **Skill** 和可选的 **MCP server** 组成；安装名统一为
`qwen-mm-plugins-<capability>`。

| 能力 | 用途 | 主要依赖 | Cookbook |
|---|---|---|---|
| `core` | 读取图片和视频；可视化文档、代码、数据、3D 文件等 | 无需 API key；音视频需要 ffmpeg；其他格式按需安装应用 | [Cookbook](cookbooks/core/usage.md) |
| `api` | Qwen VL/Omni 视觉理解、OCR、grounding、ASR、分割与音视频理解 | DashScope；本地音视频需要 ffmpeg | [Cookbook](cookbooks/api/usage.md) |
| `search` | 网页搜索、页面抽取和反向图像搜索 | Serper、Exa 或 Tavily key；反向图搜需要 Serper | [Cookbook](cookbooks/search/usage.md) |
| `video-memory` | 为长视频问答构建层次化记忆 | DashScope；构建需要 ffmpeg/ffprobe | [Cookbook](cookbooks/video-memory/usage.md) |
| `video-edit` | 图片、视频、音频生成与剪辑工作流 | DashScope；完整剪辑需要 ffmpeg、Node/Chromium | [Cookbook](cookbooks/video-edit/usage.md) |
| `blender` | 在 Blender 中完成建模、材质、灯光与渲染 | Blender；无界面 Linux 需要 Xvfb | [Cookbook](cookbooks/blender/usage.md) |
| `freecad` | 参数化 CAD、STEP/STL 与 FEM 工作流 | FreeCAD；FEM 需要 CalculiX；无界面 Linux 需要 Xvfb | [Cookbook](cookbooks/freecad/usage.md) |
| `edu-agent` | 生成中文数理讲解视频与交互页面 | 纯 Skill；Node/Chromium、ffmpeg；视频旁白需要 DashScope | [Cookbook](cookbooks/edu-agent/usage.md) |
| `proxy` | 本地协议代理，给纯文本模型看图（拦截图片 → VLM 转文字 → 转发文本） | VLM key + 一个文本模型上游端点 | [视觉代理](#视觉代理vision-proxy给纯文本模型看图零基础快速开始) |

> 说明：`proxy` 以**本地 HTTP 代理（常驻服务）**形态交付，不是 Skill + MCP server；
> 详见下方独立的[视觉代理](#视觉代理vision-proxy给纯文本模型看图零基础快速开始)章节。

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

通过安装器的 **Configure** 和 **Verify** 操作设置凭证并检查依赖。系统要求见
[安装文档](docs/zh/installation.md#依赖)，全部设置见[配置参考（英文）](docs/en/configuration.md)。

## 文档

- [安装](docs/zh/installation.md)
- [配置参考（英文）](docs/en/configuration.md)
- [贡献指南](CONTRIBUTING.md) · [本地开发](docs/zh/local_development.md)
- [添加能力](docs/zh/how_to_add_new_capability.md) · [测试](docs/zh/testing.md)

## 视觉代理（vision-proxy）：给纯文本模型看图（零基础快速开始）


**它做什么**：本机起一个常驻服务（默认 `127.0.0.1:8787`）。Claude Code / Codex / Qwen Code 把请求发到它，
它拦下图片 → 用视觉模型（VLM，如 mimo-v2.5）转成一段文字描述 → 把「文字」转发给上游文本模型。
上游只收到文字，所以纯文本模型也能"看懂"图。

**代理支持什么**
- **入站节点类型**：`responses`、`chat` 和 Anthropic（`/v1/messages`）三种。**纯自动识别，没有配置项**：先按请求路径匹配（`/v1/messages`→Anthropic、`/v1/responses`→Responses、`/v1/chat/completions`→Chat），路径认不出再按请求体结构兜底（有 `input` 字段→Responses、有 `messages` 列表→Anthropic、否则 Chat）；两者都认不出则返回 400。
- **模型类型**：**能看图的 VLM 模型**（图片原样透传）与**纯文本模型**（图片被转成文字描述）都支持；配置文件里的 `model_capabilities` 决定谁是哪种。
- **上游模型识别机制**：模型名 → 看图/纯文本的映射写在代理配置文件里。每次 `start` 会**自动扫描**三处 harness 配置文件（Claude Code / Codex / Qwen Code），把发现的模型按 harness 分组，只对**新出现的模型**交互询问你确认（默认纯文本，最安全）；已确认的模型静默复用。可用 `qwen-mm-plugins-proxy models` 随时查看或修改这份映射。

**你需要准备**：
1. 一个能看图的 VLM 的 API Key（mimo / qwen-vl / 豆包等，填在 `vlm.api_key`）；
2. 文本模型与视觉模型（VLM）的上游端点：**两端都可以是 OpenAI 兼容（Chat）或 Anthropic 格式，文本侧还额外支持 Responses 格式**。文本侧填在 `relays[].base_url`、协议由 `relays[].protocol` 指定；VLM 侧填在 `vlm.base_url`、格式由 `vlm.format` 指定。Volcengine / DeepSeek 等都可以，只要端点支持其中一种格式。

**三步开始**：
1. 编辑 `~/.qwen-mm-plugins/proxy.json`（不存在就新建），按下面模板填（key 用你自己的）：

```json
{
  "server": { "bind_port": 8787 },
  "relays": [
    { "name": "my-text", "protocol": "chat",
      "base_url": "https://<你的上游端点>", "api_key": "<你的上游KEY>", "models": ["*"] }
  ],
  "vlm": {
    "model": "mimo-v2.5",
    "base_url": "https://<你的VLM端点>", "api_key": "<你的VLM_KEY>", "format": "chat"
  },
  "model_capabilities": { "global": { "minimax-m3": "vision", "doubao-seed-2.1-turbo": "vision" } }
}
```

2. 启动：`qwen-mm-plugins-proxy start`（首次会交互引导你确认各模型是否支持图片；之后 start/stop 自动接线/还原，不再打扰）。
3. 验证：在 Claude Code / Codex / Qwen Code 里贴一张图问「这是什么」，然后 `qwen-mm-plugins-proxy logs` 出现
`injected:1` 即为成功。

**配置文件只在 `start` / `stop` 时被改写**：`start` 会备份并改写三处 harness 的 base_url 指向本代理，`stop` 时还原。服务运行期间代理**不会监听、也不会改写任何配置文件**——你运行中手动改了 harness 配置（如换模型、换 relay）或 `proxy.json`，代理不会感知、也不会去修；改动要等下一次 `start`（或重启代理）才生效。

**常用命令**：`start` / `stop` / `status` / `logs` / `check` / `models`(改模型能力) / `models-scan` / `test-image`。

> 更完整的分步说明（含三层拓扑、CC Switch / Codex++ 共存、故障排查）见
> `docs/superpowers/plans/2026-08-16-proxy-phase1-manual-test.md`。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。Blender 与 FreeCAD 集成的第三方署名分别见
[Blender NOTICE](src/capabilities/blender/NOTICE.md) 和 [FreeCAD NOTICE](src/capabilities/freecad/NOTICE.md)。
