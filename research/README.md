# research / 调研文档

本目录存放 Qwen-MM-Plugins 的调研资料，聚焦"给纯文本 Agent harness 补视觉能力"的生态与实现形态。

## 文档索引

| 文档 | 日期 | 主题 |
|---|---|---|
| [dsh-vision-plugins-survey.md](dsh-vision-plugins-survey.md) | 2026-08-16 | **DSH 视觉插件生态调研**：30+ 个 DeepSeek Harness 视觉插件的作用层分类（7 层 / 6 范式）、与 Skill/MCP 工具派、代理派（proxy 设计 / Codex++）的对比，及对 proxy 设计的启示 |
| [../RESEARCH.md](../RESEARCH.md) | 2026-08-12 | 前置调研：Claude Code / Codex 生态的视觉方案（Qwen-MM-Plugins、Codex++ PR #1550、claude-vision-skill、claude-video-vision） |

## 调研脉络

1. **2026-08-12 · RESEARCH.md**：harness 通用调研，得出"透明代理改写 / Agent 工具 / 指令脚本"三派。
2. **2026-08-13 · proxy 设计稿**：基于 Codex++ PR #1550 的代理派思路，设计 Qwen-MM-Plugins 的 proxy capability（协议归一化 + 图片安全网 + fail-open）。
3. **2026-08-16 · 本目录**：DSH 发布后视觉插件爆发，调研其作用层与实现范式，验证/修正 proxy 设计的前提。

## 核心结论（详见 dsh-vision-plugins-survey.md）

- DSH 视觉插件作用在 **7 个层**：外部本地计算（L0）、Web GUI 发送接管（L1）、附件准入（L2）、LLM 适配器包装（L3）、模型工具（L4）、子代理（L5）、MCP 桥（L6）。
- **L3 进程内适配器包装是 DSH 生态主流**（约 1/3 插件），它本质是"代理派"在 harness 内的原生实现：拦截图片→VLM→替换→透传，与 proxy 设计目标一致，但省掉了协议归一化层。
- **与 Skill/MCP 工具派**：DSH 社区验证了"依赖模型调工具不够稳健"，纯工具派普遍升级为结构化证据或与自动通道混合。
- **与外部代理派**：DSH 多了一道 `inputModalities` 附件准入闸门（Claude Code/Codex 没有），未来若支持 DSH 客户端需在 proxy 能力判定层补充"准入声明"。
