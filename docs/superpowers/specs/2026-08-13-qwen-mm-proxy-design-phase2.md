# Qwen-MM-Plugins 代理安全网设计（proxy capability）— 第二阶段（Phase 2）

> 日期：2026-08-16
> 前置：第一阶段见 [`2026-08-13-qwen-mm-proxy-design.md`](2026-08-13-qwen-mm-proxy-design.md)（本文件是它的第二阶段）
> 状态：设计稿（待用户审阅）
> 范围：第一阶段完成后的全部延后项：**OpenCode 一等接入、DSH 兼容（provider 路由形态 + 准入声明）、Claude Code hooks、Web UI 控制面板、磁盘缓存与 Phase 2 后台深缓存、跨协议流式（Responses ↔ 其他）**，及可选方向（relay 轮转、TRAE/Kimi hooks、服务端部署、未知模型启发式学习）。

---

## 1. 第二阶段范围总览

| 项 | 目标 | 触发条件/前置 | 对应主 spec 章节 |
|---|---|---|---|
| §2 OpenCode 一等接入 | 第三个一等 harness（OpenAI Chat 入站） | Phase 1 完成、Qwen Code 验证通过 | §8.2 接入 |
| §3 DSH 兼容 | provider 路由/适配器形态接入 + 准入声明 | 上游 DSH 生态成熟度、主 spec §6.6 | §1 / §6.6 / §8.2 |
| §4 Claude Code hooks | 代理覆盖不到的通道兜底 | 需覆盖"不走代理的流量"（如旧会话） | §7.1 |
| §5 Web UI 控制面板 | 控制面可视化 | 8788 JSON 管理 API 稳定 | §8.1 |
| §6 磁盘缓存 + Phase 2 后台 | 跨会话省 VLM 费用、深层图预缓存 | 内存缓存 + LRU 稳定 | §5.1 / §5.5 |
| §7 跨协议流式 | Responses ↔ Chat / Anthropic 流式互转 | 同协议直通 + Anthropic↔Chat 验证通过 | §4.3 |
| §8 可选方向 | relay 轮转 / TRAE·Kimi hooks / 服务端部署 / 未知模型学习 | 按需 | 各处 |

> 本文件各项均为独立增量，可单独排期，不强依赖彼此（§3 DSH 与 §2 OpenCode 无耦合）。

---

## 2. OpenCode 一等接入

### 2.1 事实基线（已核实）

- OpenCode 是**配置文件型** harness：`~/.config/opencode/opencode.json`（或项目级 `opencode.json`），无 base_url 环境变量。
- 自定义 provider 形态（Vercel AI SDK 生态）：`"npm": "@ai-sdk/openai-compatible"` + `"options": { "baseURL": "http://127.0.0.1:8787/v1" }`，模型列表 `models` 逐条声明（`name` / `tool_call` / `reasoning`）。
- **入站协议 = OpenAI Chat**（`/v1/chat/completions`）：复用主 spec 协议矩阵，无需新增协议；流式走同协议直通即可覆盖。
- OpenCode 的截图类工具常把图**存盘返回路径**（而非协议内图片块）——对应主 spec §5.3 形态边界，第二阶段需评估是否纳入"路径图"（见 §2.3）。

### 2.2 接入设计

- `install.sh` 改写 `opencode.json`：在 `provider` 下新增/改写自定义 provider（`baseURL` 指向探测到的代理端口），模型目录透传；改写前备份，`uninstall` 回滚（与主 spec §8.2 同机制）。
- 冲突检测：改写前读 provider baseURL 归属，若已被其它代理占用，提示链路顺序（主 spec §4.4）。
- 验收：OpenCode 贴图 → 收到 `[图片描述]` 注入后的文本模型回答（对应主 spec §10.5 验收清单第 3 项补做）。

### 2.3 待定项

- **路径图是否纳入**：OpenCode 工具返回路径形式图片时，是否由代理读文件 → VLM → 替换。倾向：默认不纳入（保持"协议内通道"边界，路径图交 MCP 工具派/Skill），除非实测 OpenCode 生态大量依赖路径图。决策前先做一次真实工具盘点。

---

## 3. DSH 兼容（provider 路由/适配器形态 + 准入声明）

### 3.1 落地形态：进程内适配器包装，而非 base_url 改写

- **DSH 没有 base_url 概念**——外部 HTTP 代理的 base_url 改写机制不适用；等价形态是注册 provider 路由 + 声明 `inputModalities`（即 DSH 生态主流，调研见 `research/dsh-vision-plugins-survey.md`）。
- 上游 2026-08 已把 DSH 纳入 manual harness：`$DSH_HOME/skills` 复制 + `$DSH_HOME/profiles/web/cordis.patch.yml` 注册 `@deepseek-ai/dsh-mcp-client`（无原生 Skill/MCP 安装 verb）。
- 因此 DSH 上的"代理派"落地有两种候选，第二阶段评估后二选一：
  - **A. 复用主代理**：DSH 的 MCP 客户端指向一个包装命令（把 DSH 请求转成 HTTP 打到 127.0.0.1:8787），代理仍以 HTTP 形态服务——需新增"DSH MCP → HTTP"适配层。
  - **B. 按 DSH 生态形态做进程内适配器**：注册 `deepseek-vision` 类 provider 路由，`stream()` 里做图→文字→透传（参考 `dsh-vision-recognizer` / `dsh-deepseek-vision` 的公开实现）。
  - 倾向：**B**（与 DSH 社区主流一致、无需维护 MCP→HTTP 桥），但实现语言/形态（Python MCP server vs JS 插件）需在第二阶段评估仓库能力约束。

### 3.2 准入声明（主 spec §6.6 的完整设计）

DSH 在图片进入会话前按模型 `inputModalities` 拒绝（`MODEL_DOES_NOT_SUPPORT_IMAGES`）——**纯改 base_url 拦不到图，图片根本进不了请求**。准入声明设计：

- 能力判定表新增可选字段 `admission_declare: true`（默认缺省）：表示该 relay 需要向客户端"声明支持图片输入"才能让图片进请求。
- 声明机制因客户端而异：DSH 是 provider 路由的 `inputModalities: ['text', 'image']` 声明；其他客户端若存在等价闸门按各自 seam 实现。
- 判定结果缓存（主 spec §6.4）同步缓存该字段，避免每请求重复匹配。
- **安全语义（硬不变量）**：启用准入声明后，纯文本模型收到的图片块必然先过安全网，不会裸传到上游——声明与安全网是一对，缺一不可（参考 `dsh-vision-guard` 把"反死锁"当硬不变量的做法，避免"声明了图片输入却没装安全网"导致会话 400 死锁）。

### 3.3 DSH 上 MCP 图片块被丢弃（上游实测约束）

- 上游实测记录：DSH 0.1.0-rc.6 经 `@deepseek-ai/dsh-mcp-client` 接入 MCP 时，**保留 text/structured 结果，但把 image/audio/resource 块替换为 `content discarded`**。
- 直接影响：DSH 上 `core` 能力（返回 image block 的 `read_image`/`visualize` 等）**不可用**；`api` 能力（返回文字）是唯一可用路径。
- 结论：「图→文字」在 DSH 上不是可选优化而是**唯一可行形态**——proxy 安全网（图在 IR 层转文字）与 api 能力天然契合，二者在 DSH 上互为表里；DSH 上任何兜底只能走文字通道，不得依赖 image block 回传。

### 3.4 进程内 vs 进程外对照（设计依据）

- 进程内方案（DSH 主流）：免协议归一化、天然与 relay 共存，但绑定 harness。
- 进程外（本设计）：跨 harness（CC/Codex/Qwen Code 一套代码），优势是上下文隔离零成本（图在 IR 层即替换）、不依赖 harness 特定 seam；劣势是自维护协议归一化与进程生命周期。
- DSH 生态完整调研见 `research/dsh-vision-plugins-survey.md` §4.2。

---

## 4. Claude Code hooks（参考 cc-vision-hook）

代理是第一跳、100% 覆盖 CC 流量，hooks 属冗余兜底，但可覆盖"不走代理的流量"（如代理未启用时的旧会话、用户临时绕过 base_url 的场景）：

- `UserPromptSubmit`：用户粘贴图片时从 image-cache 目录取新图 → VLM 转文字 → `additionalContext` 注入。
- `PostToolUse`：从 `tool_response` 抽图（兼容 MCP content-block 数组、Read 对象结构、Bash data URI）→ VLM 转文字 → `additionalContext` 注入。
- 内容哈希 + 缓存，同一图只描述一次。
- **已知边界**：hooks 只能**追加** `additionalContext`、不能删除原图；对协议级硬拒绝图片的模型无效，必须靠代理。因此 hooks 定位是"辅助兜底"而非主机制。
- 开关：`enable_hooks`（默认开）；已走代理的图片按内容哈希去重、不重复描述，避免「代理 + hooks 双通道」重复调用 VLM。

---

## 5. Web UI 控制面板（主 spec §8.1 控制面）

- 控制面 `127.0.0.1:8788` 已暴露 **JSON 管理 API**（路由开关、路由态、status、Tier1/Tier2 测试），第一阶段由 CLI 消费。
- 第二阶段加一个自包含 HTML/JS 前端（无构建链，直接消费同一套 JSON API），harness 完全感知不到控制面。
- 设计不变式：数据面 8787 稳定，UI 只是管理 API 的消费者；后期换独立前端（Rust/Tauri 或任何形态）只换 UI、不碰数据面。
- 若第二阶段仍不想维护 UI，维持纯 CLI 即可（`test-image` 已覆盖 Tier1/Tier2 测试）。

---

## 6. 磁盘缓存与 Phase 2 后台深缓存

- **磁盘缓存（`cache_disk=true`）**：按图片内容哈希落盘 + TTL，重启后命中，跨会话省 VLM 费用；结构化键沿用主 spec §5.5（`(URL)` / `(URL, 问题)`，不用 hash 值）。
- **Phase 2 后台深缓存**：当 `X > 10` 时，收集黄金窗口外、分析深度内、未缓存的深层图，异步调 VLM **只写缓存、不注入当前消息**；失败静默，缓存保持未命中待后续请求重试（主 spec §5.1）。
- 两者都吃主 spec §5.7 上下文预算，需在开启后实测 `AVG_DESC_BUDGET`。

---

## 7. 跨协议流式（Responses ↔ 其他）

- 第一阶段流式限定：同协议直通 + Anthropic ↔ Chat。
- 第二阶段补齐 Responses 相关的跨协议流式事件翻译：
  - `Responses ↔ Chat`：`response.output_text.delta` ↔ `choices[].delta.content`。
  - `Responses ↔ Anthropic`：`response.output_text.delta` ↔ `content_block_delta.text_delta`。
- 若只覆盖"Codex 入站 → Chat/Anthropic 上游"，是 Codex 用户最常碰到的路径，优先做 `Responses 入站 → Chat 上游`。

---

## 8. 其他可选方向（按需排期）

- **relay 多供应商轮转**：Codex++ 的 Aggregate 能力（多 key / 多供应商按配额轮转），不属于安全网目标，仅当用户有聚合需求时做。
- **TRAE / Kimi Code 等 harness hooks**：接入方式各异（各 harness 的 hook 规范不同），按用户实际使用排期。
- **服务端部署**：当前只做本地 127.0.0.1 代理；如需服务端形态（多用户共享），需补 TLS、鉴权、进程守护。
- **未知模型启发式学习**：未知模型默认拦截会引入额外 VLM 成本，可加"按供应商/名称启发式自动学习"（如首次成功后记住能力）。

---

## 9. 第二阶段风险与开放问题

- DSH 的落地形态（§3.1 方案 A vs B）取决于仓库能力约束（Python MCP server vs JS 插件），需在第二阶段开题时先做可行性验证。
- OpenCode 路径图是否纳入（§2.3）需真实工具盘点后再定，避免范围蔓延。
- hooks 只能追加不能删除原图，对协议级硬拒绝图片的模型必须靠代理——hooks 永不能替代代理。
- DSH 上 MCP 图块被丢弃：DSH 的任何兜底只能走文字通道，不得依赖 image block 回传（§3.3）。
- `support_cua` 分支（未合并）若未来合入上游，需复核 proxy 与 cua 的入口/import 命名无冲突（主 spec §8.1）。

---

## 10. 第二阶段决策记录（开题时确认）

- OpenCode 一等接入：入站复用 OpenAI Chat，`opencode.json` 改写 + 备份回滚（待定：路径图是否纳入）。
- DSH 兼容：倾向进程内适配器包装（方案 B）+ 准入声明（`admission_declare` + 安全语义硬不变量）。
- hooks / Web UI / 磁盘缓存 / 跨协议流式：按主 spec 对应章节实施，各自独立排期。
- relay 轮转、TRAE/Kimi、服务端部署：按需，不做承诺。
