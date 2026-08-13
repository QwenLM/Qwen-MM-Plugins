# 多模态 Agent 插件研究文档

> 研究日期：2026-08-12
> 研究对象：给 Agent harness（Claude Code / Codex 等）补上视觉与多模态理解能力的一类开源项目。
> 数据来源：各仓库公开 README、目录结构、核心源码与 PR 描述，均为远程只读分析。

## 结论摘要

这类项目做的本质是同一件事：**主模型看不懂图片/视频时，借助外部视觉模型或本地感知工具，把视觉信息转成主模型能消费的输入**。差异主要在于“挂载在哪一层”：

1. **透明代理改写**：在请求层拦截图片、调用 VLM 生成文字描述后再转发（Codex++ PR #1550）。
2. **Agent 工具 / Skill**：让主模型主动调用外部工具获得视觉结果，按“返回图片块还是返回文字”又分两类：
   - 返回文字描述：`claude-vision-skill`（轻量脚本）、Qwen-MM-Plugins 的 `api` 能力。
   - 返回图片块：Qwen-MM-Plugins 的 `core` 能力、`claude-video-vision`（面向本身支持视觉的模型）。

## 一、Qwen-MM-Plugins（本仓库）

- **仓库**：[QwenLM/Qwen-MM-Plugins](https://github.com/QwenLM/Qwen-MM-Plugins)
- **归属**：阿里 Qwen 官方组织，Apache-2.0，Python。
- **形态**：Monorepo + 安装器，把每个能力打包成 **Agent Skill + 可选 MCP server**，以插件市场形式安装到 Claude Code、Codex、Qoder、OpenClaw、Qwen Code、Gemini CLI 等。
- **关键能力**：
  - `core`：本地能力，无 API key。`read_image` / `read_video` / `visualize` / `media_info` 等 MCP 工具，把图片、视频帧、PDF、Office、3D 等渲染成 image block 或文本，适合本身支持视觉的模型。
  - `api`：云端能力，调用 DashScope 的 Qwen VL / Omni / ASR / SAM3，工具如 `vision_chat`、`ocr`、`grounding`、`omni_*`、`transcribe_audio`，返回文字或结构化结果，适合纯文本主模型。
  - 另有 `search`、`video-memory`、`video-edit`、`blender`、`freecad`、`edu-agent` 等能力。
- **实现方式**：不是改模型也不是透明改请求，而是给 agent 装上“工具”：模型按 Skill 指引调用 MCP 工具，视觉理解发生在工具内部（本地渲染或 Qwen 云模型），结果以 MCP text/image 块返回。
- **对纯文本模型**：核心路径是 `api` 能力，由外部 VLM 把图/视频理解成文字再交给主模型。

## 二、Codex++ PR #1550

- **仓库**：[BigPizzaV3/CodexPlusPlus](https://github.com/BigPizzaV3/CodexPlusPlus)，PR [pull/1550](https://github.com/BigPizzaV3/CodexPlusPlus/pull/1550)
- **归属**：社区个人项目（CodexApp 增强工具），AGPL-3.0，Rust。PR 作者账号为 `jarvislee90s-dot`。
- **形态**：CodexApp 增强工具内嵌**本地透明代理**（约 `127.0.0.1:57321`），PR 状态为 open、待合并。
- **实现方式**：
  - 拦截发往模型的上游请求；对标记为纯文本的模型，把请求中的 image blocks 以及工具返回的 base64 图片抽取出来。
  - 交给独立配置的 VLM（支持 Chat Completions / Responses 双格式）生成文字描述（历史轮按 URL 缓存、当前轮按 URL+问题缓存）。
  - 把描述替换回请求、剥离原始图片，再转发给纯文本主模型；VLM 失败时 fail-open，注入“看不到图”的系统提示。
  - 另包含 reasoning 字段剥离、上下文溢出保护、UI checkbox、VLM 测试面板、埋点等。
- **对纯文本模型**：自动、无感，主模型只看到文字描述；但依赖 Codex 流量走代理，PR 自身也记录了默认 PureApi+Responses 模式下能力可能不生效的局限。

## 三、claude-vision-skill

- **仓库**：[asuojun/claude-vision-skill](https://github.com/asuojun/claude-vision-skill)
- **归属**：社区个人项目，无许可证声明，JavaScript。Star 数约 1.8k。
- **形态**：轻量脚本 + 指令文档，不是 MCP server，也不是 marketplace 插件。
- **实现方式**：
  - `vision.js`：读本地图片 → base64（或传 URL）→ 调用 OpenAI 兼容格式的 vision API（默认阿里云百炼 Qwen，`qwen3.5-omni-plus` / `qwen-vl-max`）→ 打印文字描述。
  - 支持 `--url`、`--clipboard`（macOS Swift / Windows PowerShell 读剪贴板）、`--no-fallback`。
  - `CLAUDE.md` / `SKILL.md`：告诉 agent“遇到图片不要用 Read，改用 `node vision.js <path> "描述"`”。
  - `cyberboss-setup.md`：把同样的机制接入 cyberboss 微信机器人（persona + app.js）。
- **对纯文本模型**：纯“图片→文字”单向转换，主模型全程只接触文字；成本极低、接入极简，适合 DeepSeek 等无视觉模型的快速补位。

## 四、claude-video-vision

- **仓库**：[jordanrendric/claude-video-vision](https://github.com/jordanrendric/claude-video-vision)
- **归属**：社区个人项目，MIT，TypeScript。Star 数约 1.2k。
- **形态**：Claude Code 插件（`.claude-plugin` marketplace），由 Skill + Node.js MCP server 组成。
- **实现方式**：
  - MCP server 提供 `video_watch`、`video_analyze`、`video_detail`、`video_info`、`video_configure`、`video_setup` 六个工具。
  - ffmpeg 按需抽帧（可变 fps / 分辨率 / 时间段），帧以图片块返回，Claude 直接“看”。
  - 音频并行处理：Gemini API、本地 Whisper（whisper.cpp / openai-whisper）、OpenAI API 三选一，返回带时间戳的转写和非语音事件标签。
  - 支持 YouTube URL（yt-dlp 下载，优先使用字幕/自动字幕，缺失时才走音频后端）。
  - Skill 要求先 `video_info`、长视频先 `video_analyze`，再按结构数据决定抽帧策略。
- **对纯文本模型**：音频转写对纯文本模型可用，但帧本身仍是图片块，需要主模型支持视觉；README 明确它定位为“感知层（perception layer）而非解释层”，主要面向 Claude 这类视觉模型补视频感知能力。

## 五、对比总表

| 维度 | Qwen-MM-Plugins | Codex++ PR #1550 | claude-vision-skill | claude-video-vision |
|---|---|---|---|---|
| 形态 | Skill + MCP server 插件市场 | CodexApp 内置透明代理 | 脚本 + CLAUDE.md/SKILL.md 指令 | Claude Code 插件（Skill + MCP server） |
| 介入层 | Agent 工具调用层 | HTTP 请求代理层 | 模型按指令调用脚本 | Agent 工具调用层 |
| 是否自动 | 需模型/用户触发工具 | 自动拦截改写 | 模型按指令触发 | 模型/用户触发工具 |
| 图片处理 | core 返回图片块；api 用 Qwen 返回文字 | 抽图 → 任意 VLM → 文字替换 | 图片 → vision API → 文字 | 视频抽帧 → 图片块 |
| 音频处理 | api 提供 ASR / Omni 工具 | 不覆盖 | 不覆盖 | Gemini / Whisper / OpenAI 转写 |
| 纯文本主模型支持 | 是（api 能力） | 是（核心目标） | 是（核心目标） | 部分（音频可文字，帧图仍需视觉模型） |
| 视频支持 | 是（抽帧 / Omni / video-memory） | 图片为主（工具图 base64） | 否 | 是（视频专用） |
| 支持 harness | Claude Code、Codex、Qoder、OpenClaw、Qwen Code、Gemini CLI 等 | 主要 Codex App | Claude Code 项目 + cyberboss | Claude Code |
| 主要依赖 | uv / ffmpeg / LibreOffice / DashScope 等 | Rust 代理 + 自配 VLM | Node.js + DashScope Key | Node.js + ffmpeg + 音频后端 |
| 许可证 | Apache-2.0 | AGPL-3.0 | 无声明 | MIT |
| 状态 | 官方维护，已发布 | PR open 待合并 | 活跃个人项目 | 活跃个人项目，v1.x |

## 六、路线小结

- **透明代理改写（Codex++ PR #1550）**：用户体验最“无感”，纯文本模型完全不知道视觉过程；代价是与特定 App / 代理链路深度耦合，改动面大、维护成本高。
- **Skill + MCP 工具（Qwen-MM-Plugins、claude-video-vision）**：标准、可组合、跨 harness；模型需具备工具调用能力，且结果是否可被主模型消费取决于“返回图片块还是文字”。
- **指令 + 独立脚本（claude-vision-skill）**：最轻量、可移植到任何能执行 shell 的 agent；缺少类型化工具、无插件市场管理，靠文档约定约束模型行为。

## 七、选型建议

- 需要**官方背书、多模态能力全**（图/视频/文档/音频/3D/搜索）：选 Qwen-MM-Plugins。
- 需要**在 CodexApp 内给纯文本模型自动识图**：关注 Codex++ PR #1550 的合入进度。
- 只需要**给无视觉模型快速补“看图转文字”**：选 claude-vision-skill，改一行配置即可。
- 需要在 Claude Code 里**理解视频（画面+音频）**：选 claude-video-vision。

## 参考资料

- [Qwen-MM-Plugins](https://github.com/QwenLM/Qwen-MM-Plugins)
- [CodexPlusPlus PR #1550](https://github.com/BigPizzaV3/CodexPlusPlus/pull/1550)
- [claude-vision-skill](https://github.com/asuojun/claude-vision-skill)
- [claude-video-vision](https://github.com/jordanrendric/claude-video-vision)
