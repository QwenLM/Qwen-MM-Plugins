# DSH 视觉插件生态调研：作用层与实现范式

> 调研日期：2026-08-16
> 调研对象：DeepSeek Harness（DSH）发布后涌现的、为纯文本模型补视觉能力的社区插件
> 数据来源：awesome-dsh-plugin 精选列表（2026-08 快照）+ 各仓库 README / 核心源码 / DSH 官方 capability-seams 文档，均为远程只读分析
> 上游关联：本文是 [RESEARCH.md](../RESEARCH.md)（2026-08-12，harness 通用调研）的续篇，聚焦 DSH 生态，并与 [proxy 设计稿](../docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md) 对照

---

## 0. 结论摘要（TL;DR）

DSH 发布后视觉插件爆发式出现，**几乎全部是"图片→文字转换"这一件事的不同挂载点**。调研 30+ 个插件后，它们作用在 **7 个不同的层**，对应 **6 种实现范式**：

| 作用层（从外到里：L0 最外层 → L6 最接近模型） | 实现范式 | 代表插件 | 是否自动 | 是否依赖模型调用工具 |
|---|---|---|---|---|
| L0 外部进程/本地模型 | 本地/零成本通道 | `dsh-tool-vision`（本地 VLM）、`dsh-vision`（豆包 Web/CDP）、`free-vision-skill`（macOS Vision） | 视宿主而定 | 部分是 |
| L1 Web GUI 客户端 | GUI 发送接管 | `dsh-plugin-image-input`、`dsh-subagent-vision` 的 send-time | **自动** | **否** |
| L2 附件准入层 | 准入声明 + sidecar | `dsh-plugin-multimodal`、`dsh-vision-bridge` | **自动** | **否** |
| L3 LLM 适配器层（进程内代理） | **适配器包装 / provider 路由** | `dsh-vision-recognizer`、`dsh-deepseek-vision`、`dsh-llm-vision-bridge`、`dsh-vision-proxy` 等 10+ 个 | **自动** | **否** |
| L4 工具层 | 模型主动调用的 `vision_*` 工具 | `dsh-vision-toolkit`（10 工具）、`mimo-vision`、`dsh-youreyes` 的 tool 面 | 模型触发 | **是** |
| L5 子代理层 | 委派给视觉子代理 | `dsh-vision-subagent`、`dsh-subagent-vision` | 模型触发/自动 | 混合 |
| L6 外部协议桥 | MCP 桥接 | `deepseek-vision`（MCP bundle）、`Gemini-Eyes` | 模型触发 | **是** |

三个关键结论：

1. **DSH 生态的主流答案 = L3 进程内适配器包装**（约 1/3 的视觉插件走这条路）。它和你的 proxy 设计**目标完全相同**（拦截图片→VLM→替换文字→透传），但实现位置不同：**在 harness 进程内部的 LLM adapter seam 上做包装**，而不是在 harness 之外起一个常驻 HTTP 代理。这是"代理派"在 DSH 体系内的原生落法。
2. **DSH 多了一道"附件准入"闸门**（`inputModalities` + `MODEL_DOES_NOT_SUPPORT_IMAGES`），这是 Claude Code / Codex 没有的。所有要"无感识图"的插件必须先骗过这道闸门（声明图片输入），否则图片根本进不了会话——**这改变了"代理派"的落地形态**：不能只改 base_url 就能拦到，还要处理模型准入。
3. **纯工具派（Skill/MCP，依赖模型主动调用）在 DSH 生态里仍然存在，但已明显退居二线**，且大多升级为"结构化证据"（OCR/布局/坐标 JSON），或与自动通道（L1/L3）组合成混合形态。社区共识与你的判断一致：**依赖模型调用工具不够稳健**。

---

## 1. 调研背景与基线：DSH 原生是怎么处理图片的

要回答"这些插件作用在哪一层"，先要搞清楚 DSH 原生图片路径上有哪些可插拔点（seam）。以下基于官方 `docs/capability-seams.md` 与源码核实。

### 1.1 DSH 原生图片链路

```
用户在 Web GUI 粘贴/拖入图片
  └─► [L1] dsh.client（客户端插件 / Web 层）把图片变成 composer 草稿
  └─► [L2] ctx.attachments（attachment 服务）：
          · 校验（png/jpeg/webp/gif、5MB、像素上限）
          · 持久化为 durable attachment（ImageBlock 引用）
  └─► [L2] api-proxy 准入预检：
          · 读会话当前模型的 inputModalities
          · 不含 'image' → 拒绝：MODEL_DOES_NOT_SUPPORT_IMAGES
  └─► [L3] ctx.llm 适配器：
          · llm-pi-ai（多模态适配器）：模型声明 image → 把 attachment 解析成 provider 原生图片内容
          · llm-deepseek（纯文本适配器）：遇到 image block → 抛 UNSUPPORTED_CONTENT
  └─► [L4] tool-fs 的 read_image 工具：
          · 也读 inputModalities 门禁；返回 image block 进上下文
  └─► [L6] 外部 MCP server（可桥接）
```

**关键事实**：

- DSH 本身**已经支持多模态模型**（`llm-pi-ai` 适配 Gemini 等），图片是"一等公民"（durable attachment），不是完全没有图片通道。
- 但对 DeepSeek 官方纯文本模型（`llm-deepseek`），图片路径上有**两道硬闸门**：api-proxy 的 `MODEL_DOES_NOT_SUPPORT_IMAGES`（准入拒绝）和 llm-deepseek 序列化器的 `UNSUPPORTED_CONTENT`（请求时拒绝）。
- 因此"给纯文本模型识图"的本质 = **在两道闸门之间/之前，把图片提前转成文字**。哪一层来做这件事，就是插件的分野。

### 1.2 DSH 的插件 seams（视觉相关）

| seam | 角色 | 视觉插件怎么用它 |
|---|---|---|
| `dsh.client` | 浏览器端客户端插件 | L1：发送接管、粘贴转路径、右面板 UI、设置卡片 |
| `ctx.attachments` | durable 图片存储 | 读 `readImage(ref)` 取字节喂给 VLM；把生成图存回会话 |
| `ctx.llm` | LLM 适配器注册表 | L3：`registerAdapter` 注册新路由、包装 `stream()`、`resolveModel` 覆盖 `inputModalities` |
| `ctx.tools` | 工具注册表 | L4：注册 `vision_*` / `describe_image` / `analyze_image` 工具 |
| `ctx.subagents` | 子代理 | L5：`start('spawn', {agentOptions})` 委派给视觉模型子代理 |
| `ctx.skills` | Skill 目录 | 给模型注入"何时调用视觉工具"的指引 |
| `ctx.credentials` / `ctx.settings` | 凭据/设置 | 视觉 API key 存官方凭据库；设置卡片写 `settings.yaml` |
| `ctx.webServer` | HTTP 路由 | 插件自带 `/plugins/xxx/analyze` 等本地路由（GUI 转文字的后端） |
| `ctx.compaction` | 上下文压缩 | 压缩时复用哪个 provider（决定含图历史是否还会被桥接） |

---

## 2. 全景清单：30+ 个 DSH 视觉插件分桶

> 只列"给模型补视觉输入"的插件；纯 UI 渲染（`dsh-inline-images`）、纯生图（`dsh-chat-imagine`、`dsh-draw-router`）、纯 OCR 服务（`dsh-nuphus-mcp` 的 PaddleOCR）等不列入主表，但在范式中会提及。

### A. L3 适配器包装 / provider 路由（自动、进程内代理）——**主流**

| 插件 | 一句话 | VLM 后端 | 特点 |
|---|---|---|---|
| [kaixinbaba/dsh-vision-recognizer](https://github.com/kaixinbaba/dsh-vision-recognizer) | 注册 `vision-recognizer` 路由，包装真实 DeepSeek adapter，stream 中逐图转文字 | 15+ 供应商、双协议（OpenAI/Anthropic）、Ollama 自动探测 | fallback 链、60s 冷却、内容哈希缓存、设置 UI |
| [siegfly/dsh-deepseek-vision](https://github.com/siegfly/dsh-deepseek-vision) | 注册 `deepseek-vision` 网关路由，继承官方 DeepSeekAdapter，`stream()` 里 ImageBridge 改写图片块 | 默认 qwen3-vl-flash（DashScope），任意 OpenAI 兼容 | 明确 fail-closed 语义、session log 保留原图、附件上限预检 |
| [Flyvhidbwo/dsh-vision-proxy](https://github.com/Flyvhidbwo/dsh-vision-proxy) | 注册 `deepseek-vision` 路由（"DeepSeek + 自动识图"），自动转译 | 默认 qwen3.7-flash，Ollama 零配置 | 20s 硬超时防挂、端点冷却、sharp 降采样、无 key 快速失败 |
| [Einskyle/dsh-llm-vision-bridge](https://github.com/Einskyle/dsh-llm-vision-bridge) | 原生 LLM provider 桥：`deepseek-vision` 路由声明 `[text,image]`，`stream()` 里嵌套 `ctx.llm.stream()` 调视觉 provider | 默认本地 llama.cpp Qwen3-VL，可换任意 OpenAI 兼容 | 503 重试、LRU 描述缓存、compaction 复用 |
| [NagasakiSoyo-ui/dsh-llm-deepseek-vision](https://github.com/NagasakiSoyo-ui/dsh-llm-deepseek-vision) | 视觉增强适配器：`deepseek-vision` 路由，带图才走视觉模型 | 默认 mimo-v2.5（opencode-go） | 无图零开销直通；注意：视觉描述每轮重生成（无缓存） |
| [haiziyao/dsh-vision-mix](https://github.com/haiziyao/dsh-vision-mix) | `Mix` 路由：文本基础模型 + 视觉模型 + 生图 API 组合 | 复用 DSH 已配 provider（含中转站） | 跨轮追问、意图识别、能力测试门（先发红图验证再声明）、识图/生图会话记录 |
| [jyh20030112/dsh-visual-plugin](https://github.com/jyh20030112/dsh-visual-plugin) | `deepseek-vision` 包装适配器 + 右侧"视觉桥接"面板 | 任意 OpenAI 兼容 | 会话内展示描述卡片、`vision_describe` 工具、面板实时轮询 |
| [shinjiyu/dsh-plugin-multimodal](https://github.com/shinjiyu/dsh-plugin-multimodal) | 补"贴图准入"：GUI 收图，sidecar 转文字；原生视觉模型直通 | 任意 OpenAI 兼容 | 明确"不假装是视觉工具箱"，`see_image` 工具看磁盘图 |

> 注：`shinjiyu/dsh-plugin-multimodal` 偏 L2 准入 + sidecar，但精神上与 L3 一致（自动、不依赖模型调工具），归此桶更接近社区心智。

### B. L1/L2 自动接管（GUI 发送接管 / 准入补丁）

| 插件 | 作用层 | 机制 |
|---|---|---|
| [Elohia/dsh-plugin-image-input](https://github.com/Elohia/dsh-plugin-image-input) | L1（GUI） | 在 Web 输入框拦截 Enter/发送（捕获阶段），把 blob 图片经本地路由 `/plugins/mmv/analyze` 转文字后，把"文字+描述"一起发出；原生视觉模型放行 |
| [ximengxiaolan/dsh-vision-bridge](https://github.com/ximengxiaolan/dsh-vision-bridge) | L2+L3 | 补丁 `ctx.llm.resolveModelInfo` 声明图片输入（准入放行）+ 包装 `llm.streamWithRegistration`，发模型前扫描图片块转文字 |
| [niuniuaba/dsh-subagent-vision](https://github.com/niuniuaba/dsh-subagent-vision) | L1+L5 | 发送时把草稿图 POST 到 `/subagent-vision/paste` 变私有临时路径拼进 prompt（不触发准入），模型再委派给视觉子代理 |

### C. L4 工具派（模型主动调用）——含"结构化证据"升级版

| 插件 | 工具 | 特点 |
|---|---|---|
| [Anionex/dsh-vision-toolkit](https://github.com/Anionex/dsh-vision-toolkit) | 10 个 `vision_*` 工具（Q&A/OCR/grounding/pixel diff/UI 还原…）+ `vision-tools` Skill | 意图感知 Q&A、原始像素坐标、Artifacts、渐进暴露（先 `vision_toolkit_activate`） |
| [ysr666/dsh-vision-router](https://github.com/ysr666/dsh-vision-router) | 11 个像素工具（`vision_ground/crop/describe/pixel_diff`…）+ 可选自动路由 | 内置免 key OVHcloud 匿名链；"贴图即用"；图像轮自动切视觉、文本轮切回 DeepSeek |
| [liustack/modlens](https://github.com/liustack/modlens) | `modlens_read_image` 工具 + 粘贴转路径 + 每路由一个 `(modlens vision)` 包装入口 | 结构化 JSON 证据；DSH 第一个视觉插件 |
| [good-boy4069/dsh-vision-guard](https://github.com/good-boy4069/dsh-vision-guard) | `vision_analyze` 工具 + agent/pre-step 准入改写 + llm/stream 兜底 | **图片在写进 session log 前就转文字**（防 400 死锁）、能治愈已死锁会话、反死锁硬不变量 |
| [gloryxpnv/dsh-tool-vision](https://github.com/gloryxpnv/dsh-tool-vision) | `vision` 工具 + 可选 `vision-bridge` 服务 | 本地优先（LM Studio/Ollama），强制 JSON 模板证据（summary/ocr/layout/semantics/uncertainty），反幻觉 |
| [wulusai2333/mimo-vision](https://github.com/wulusai2333/mimo-vision) | `describe_image` 工具 | mimo-v2.5 走 opencode Zen API，免费路由优先、付费兜底，ImageMagick 转码 SVG/TIFF/HEIC |
| [54xkeee/dsh-youreyes](https://github.com/54xkeee/dsh-youreyes) | `vision` 工具 + 包装适配器（混合） | 反重力/任意 OpenAI 兼容/Gemini/本地 Ollama 多通道、档位自动升级、视觉证据记忆、内容哈希缓存 |
| [FuzzySoul/dsh-free-vision](https://github.com/FuzzySoul/dsh-free-vision) | 识图工具 | 免费档（Qwen3-VL-Flash/Doubao/DeepSeek-OCR/GLM），设置 UI，无 MCP 配置 |
| [TZHR-invest/dsh-plugins#dsh-vision-tool](https://github.com/TZHR-invest/dsh-plugins/tree/main/packages/dsh-vision) | `vision` 工具 | 任意 OpenAI 兼容端点，可选多模型 cross-check 防幻觉，无内置 key |
| [Elohia/dsh-plugin-mm-vision](https://github.com/Elohia/dsh-plugin-mm-vision) | `mm_vision` 工具 | 通感编码器：把图片转成"画布/元素/百分比坐标"结构化空间文字 |
| [xiaoshihou514/dsh-vision](https://github.com/xiaoshihou514/dsh-vision) | 原生极简视觉工具 | 智谱免费 / 本地 Qwen-VL 2B |
| [niyongsheng/free-vision-skill](https://github.com/niyongsheng/free-vision-skill) | `view_image` / `ocr_image` | macOS Vision Framework 本地 OCR/描述，图片不出本机 |
| [linenxi-ctrl/dsh-vision](https://github.com/linenxi-ctrl/dsh-vision) | `recognize_image` / `screenshot` 工具 + 鲸鱼按钮面板 | 四协议自动探测（OpenAI/Responses/Anthropic/Gemini），识图结果自动回传会话 |
| [akqwpeter-prog/dsh-media-skills](https://github.com/akqwpeter-prog/dsh-media-skills) | 粘贴识图 + 生图 | GLM-4V-Flash + Gemini 引擎 failover、ModLens 风格结构化证据 |

### D. L5 子代理委派

| 插件 | 机制 |
|---|---|
| [ruby1304/dsh-vision-subagent](https://github.com/ruby1304/dsh-vision-subagent) | `vision_agent` 工具委派给一次性视觉子代理（MiniMax/Kimi），图片字节与中间上下文永不进主会话；粘贴图片走自动通道 |
| [niuniuaba/dsh-subagent-vision](https://github.com/niuniuaba/dsh-subagent-vision) | 见上 B 表：发送时转路径 + 子代理 |
| [ZEM17/dsh-subagent-agy](https://github.com/ZEM17/dsh-subagent-agy) | Google Antigravity CLI 作为子代理，图片按路径分析 |

### E. L6 MCP / 外部协议桥

| 插件 | 机制 |
|---|---|
| [GOU-GEE/deepseek-vision](https://github.com/GOU-GEE/deepseek-vision/tree/main/plugins/dsh-plugin-deepseek-vision) | 独立 MCP server（`analyze_image/analyze_clipboard/compare_images`）+ Skill，DSH bundle 托管 Python 运行时 |
| [ConsoleSun/Gemini-Eyes](https://github.com/ConsoleSun/Gemini-Eyes) | MCP 桥到 gemini.google.com 浏览器会话，免 key 视觉/生图/生视频 |

### F. 相关但不属"识图"（列出以免混淆）

- `dsh-computer-use` / `Anionex/dsh-computer-use` / `kunjinkao-os/dsh-mobile-gui-agent`：AX-tree 无障碍树优先，"零视觉成本"操作 GUI，GLM-4V-Flash 仅作截图兜底。
- `dsh-browser` / `dsh-browser-control` / `guo6x/dsh-pilot`：浏览器控制，text-first snapshot（无障碍树）替代截图。
- `dsh-companion` / `zhaoolee/notes`：PNG 长图导出（输出侧，非识图）。
- `omdsh-dev/Qwen-MM-Plugins`：本仓库的 DSH 移植 fork（见 [Models & Providers](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)）。

---

## 3. 核心问题一：这些插件作用在什么层？（详细分析）

### 3.1 七层模型（从"离模型最近"到"离模型最远"）

把 DSH 生态的落点画成一条链：

```
[外部世界]
   │
L0 外部进程/本地计算（Ollama/llama.cpp/macOS Vision/浏览器 CDP/HTTP 代理）
   │
L1 Web GUI 客户端（composer 粘贴、发送接管、右面板）   ←── dsh.client
   │
L2 附件准入（inputModalities 声明 / attachment 持久化）  ←── ctx.attachments + api-proxy
   │
L3 LLM 适配器（provider 路由包装 / stream 拦截）        ←── ctx.llm
   │
L4 模型工具（vision_* 工具，模型自己调）               ←── ctx.tools + ctx.skills
   │
L5 子代理（委派给视觉模型子代理）                      ←── ctx.subagents
   │
L6 MCP / 外部协议桥                                    ←── MCP server
   │
[主模型上下文]
```

**判断规则**（为什么这么分）：看"图片在哪一步、被谁、以什么方式变成文字"。

- 图片**在进入 harness 之前**就被转文字 → L0 / L1。
- 图片**进了 harness 但被提前转文字、主模型从未收到图** → L2/L3（自动、无痕）。
- 图片**以路径/引用形式存在，模型主动调工具去读** → L4/L5。
- 图片**发给外部 MCP server 处理** → L6。

### 3.2 各层详解

**L3 适配器包装（约 1/3 插件，事实主流）**

最典型的实现（以 `dsh-deepseek-vision` 为例）：
```
ctx.llm.registerAdapter(['deepseek-vision'], proxyAdapter)
  ├─ resolveModel → inputModalities: ['text','image']   ← 骗过 L2 准入
  └─ stream() → 扫描 messages 里的 image block
        → ctx.get('attachments').readImage(ref) 取字节
        → 调 VL 模型（base64 → /chat/completions）
        → 图片块替换成 [图片描述] 文本
        → yield* 内层适配器 stream（原生 DeepSeek wire）
```

本质是 **"在 harness 进程内的 LLM adapter seam 上做透明代理"**。它和你 proxy 设计的目标、管线（扫描→VLM→缓存→替换→透传）几乎一一对应，差异只在**挂载位置**（见 §4）。

**L2 准入层（DSH 独有）**

任何"自动识图"插件都无法绕开的一步：DSH 在图片进入会话前就按模型 `inputModalities` 拒绝。所以 L3 插件必须 `resolveModel` 覆盖声明 `[text,image]`；`dsh-vision-bridge` 甚至直接补丁 `resolveModelInfo`。**这是 Claude Code / Codex 没有的一层**，直接影响代理派的落地（见 §4.2）。

**L1 GUI 层（最"外"的自动接管）**

`dsh-plugin-image-input` 在浏览器里就拦下发送，图片根本没进 harness 的 attachment 流程。好处：不碰任何模型适配器、任何模型都能用；代价：只在 Web GUI 生效、只处理"用户粘贴"这一种来源（工具返回的图管不到）。

**L4 工具派（老范式 + 新升级）**

纯工具派在 DSH 生态仍占约 1/3，但普遍做了两个升级：
1. **结构化证据**（ModLens / tool-vision / vision-toolkit）：让 VLM 填固定 JSON（OCR/布局/坐标/实体/uncertainty），主模型引用证据而不是"转述"——缓解"模型不调工具"之外的第二个问题"描述质量不可验证"。
2. **与自动通道混合**（youreyes、vision-router、modlens、tool-vision）：既提供工具，也提供自动的准入/包装通道，两者互补。

**L5 子代理层（DSH 特色）**

DSH 的 `subagent` 是头等 seam，子代理可以路由到任意 provider。视觉插件利用这一点做**上下文隔离**：大截图、多图对比不占主模型窗口，只有最终文字回主会话。这是 Claude Code / Codex 生态少见的做法。

**L0 本地/零成本层**

- `dsh-tool-vision`：本地 LM Studio/Ollama，图片不出机器，强制 JSON 证据。
- `dsh-vision`（54xkeee）：豆包 Web 免费通道——通过浏览器 CDP 驱动你已登录的 Chrome，零 key 零成本。
- `free-vision-skill`：macOS Vision Framework，系统级 OCR，完全本地。
- `dsh-vision-router`：内置 OVHcloud 匿名免费链（2 req/min/IP），免 key。
- 趋势：**"免费优先、本地优先、key 尽量可省"** 是这批插件的一致取向，与 Qwen proxy 设计里"VLM 后端可配 Ollama"的思路一致。

### 3.3 一句话总结"作用层"

> DSH 视觉插件的作用层 = **从"外部本地模型"到"MCP 桥"的整条链都有覆盖**，但社区共识收敛在 **L3 进程内适配器包装（自动、无痕）**，其次是 **L4 结构化工具（可靠、可验证）**；纯 L1 GUI 接管和纯 L6 MCP 桥都是少数派。

---

## 4. 核心问题二：与你说的两种形式有什么不同？

你提到的两种形式：
- **A. Skill / MCP 工具派**：模型按 Skill 指引主动调用 MCP 工具（vision_chat/OCR）拿文字，依赖模型调用工具的成功率。
- **B. 代理派（你的 proxy 设计 + Codex++ PR #1550）**：在 harness 与模型之间拦截请求，抽图转文字后替换，精确、无痕、不依赖模型调工具。

### 4.1 与 A（Skill/MCP 工具派）的对比

| 维度 | A：Skill/MCP 工具派 | DSH 生态的主流（L2/L3 自动层） |
|---|---|---|
| 触发方式 | 模型主动调用工具 | 图片进会话前/进上下文前自动转文字 |
| 对"粘贴图" | **管不到**（模型看不到用户粘贴的图，除非另有准入） | 天然覆盖（L2/L3 拦的就是这些图） |
| 对"工具返回图"（read_image 截图） | 靠模型再调一个工具 | L3 在 stream 里连 tool_result 里的图一起扫 |
| 是否依赖模型工具调用能力 | **是**（工具的软肋） | **否** |
| 主模型收到什么 | 工具返回的文字 | 描述文本（自动注入） |
| 失败语义 | 工具没调用=静默失败 | fail-open：降级占位/提示，不报错不死锁 |
| DSH 生态位置 | 仍存在（约 1/3），但普遍升级为结构化证据或混合形态 | **主流**（自动接管） |

**结论**：DSH 社区用自己的行动验证了你的判断——"依赖模型主动调用工具不够稳健"。凡是主打"贴图即用"的插件，都做了自动层；纯工具派要么做结构化证据（补可靠性），要么与自动通道混合（补覆盖）。

### 4.2 与 B（代理派：你的 proxy 设计 / Codex++）的对比——**最重要的一节**

你的 proxy 设计（和 Codex++）是**外部常驻 HTTP 代理**：改写 harness 的 base_url 指向 `127.0.0.1:8787`，在协议层拦截所有请求。DSH 生态的主流（L3）是**进程内适配器包装**。两者对比：

| 维度 | 外部 HTTP 代理（你的 proxy / Codex++） | DSH 进程内适配器包装（L3） |
|---|---|---|
| 挂载位置 | harness 之外，改写 base_url | harness 进程内 `ctx.llm` seam，注册新 provider 路由 |
| 覆盖来源 | 一切走 base_url 的流量 | 走该 provider 路由的流量 |
| 协议 | 需自己实现 Anthropic/OpenAI 协议归一化（9 种转换） | 免协议转换：在 adapter 的 `stream()` 里直接拿结构化 messages，VLM 调用走 OpenAI 兼容 HTTP 即可 |
| 模型准入 | 无此概念（只要 base_url 改了就能拦） | **必须处理 inputModalities 准入**：声明图片输入，否则图片进不了会话 |
| 需要用户操作 | 改 base_url + 起守护进程 | 模型选择器里选新 provider 路由（或设默认） |
| 进程/生命周期 | 独立进程，需守护、端口、冲突检测 | 随 harness 进程，安装即注册 |
| 跨 harness | 协议代理理论上跨 harness | 绑定 DSH（但思路可移植到任意有 LLM adapter seam 的 harness） |
| 上游链路兼容（cc-switch/relay） | 需要"第一跳"铁律 + 双重剥图检测 | 无此问题（不碰 base_url，与 relay 天然共存） |
| 维护成本 | 独立协议栈 + 进程管理 | 依赖 DSH 公共 seam（rc.6 稳定） |

**关键洞察**：

1. **目标一致，位置不同**。DSH 的 L3 适配器包装 = "代理派"在 DSH 体系内的**原生实现**。它证明你的核心论点（拦截 + VLM + 替换 + 透传是正确路线）是对的，而且社区已经用 10+ 个插件验证了这条路的可维护性。
2. **DSH 的"准入闸门"是个新的设计输入**。在 Claude Code/Codex 上，代理只要改了 base_url 就能 100% 拦到；在 DSH 上不行——**图片先被 inputModalities 拒之门外**，代理根本看不到。DSH 插件解决方式是"声明图片输入"（resolveModel 覆盖），这相当于**在你的代理里加一个"模型能力声明"概念**：如果有一天你要支持 DSH 客户端，光改 base_url 不够，还得处理准入。
3. **进程内 vs 进程外是真实权衡**：
   - 进程内（DSH 主流）省掉了整个协议归一化层（这是你 proxy 设计里最重的一块），且天然与 relay/cc-switch 共存；
   - 进程外（你的设计）跨 harness（Claude Code + Codex + Qwen Code 一套代码）、不改 harness 内核、对"协议级硬拒绝图片"的模型更稳（hooks 派补不了的那类）。
   - 你的设计主打"跨 harness 通用"——这是进程内方案做不到的，也是它存在的理由；代价是协议归一化和进程管理成本。
4. **DSH 生态的 L1 发送接管和 L5 子代理是"代理派"的补充形态**，说明"自动但不改 base_url"还有第三条路：GUI 层接管（管粘贴图）和子代理委派（管上下文隔离）。你的 proxy 设计里的 hooks 兜底（Claude Code UserPromptSubmit/PostToolUse）本质上就是 DSH 的 L1/L4 混合。

### 4.3 一张图对照三种路线

```
路线一：外部 HTTP 代理（你的 proxy / Codex++）
  harness ──base_url──► 127.0.0.1:8787 代理 ──► 上游
                          │ 协议归一化 + 抽图 + VLM + 替换

路线二：DSH 进程内适配器包装（社区主流）
  harness 内 ctx.llm：deepseek-vision 路由
  stream() { 抽图 → VLM → 替换 → yield* 内层 stream }
                          │ 免协议归一化，但要骗过 inputModalities 准入

路线三：DSH GUI/子代理/工具（补充形态）
  L1 发送接管 / L4 结构化工具 / L5 视觉子代理
```

---

## 5. 对 Qwen-MM-Plugins proxy 设计的启示

调研 DSH 生态后，对你的 proxy 设计（`docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md`）有 6 点启示：

1. **核心路线验证通过**：DSH 社区 10+ 个插件用"拦截图片→VLM→替换→透传"实现了你的核心设想，且都在强调你设计里的三个点：内容哈希缓存（几乎人人都有）、fail-open/降级占位（vision-guard 甚至把"绝不 400 死锁"当硬不变量）、"图片只描述一次"。
2. **协议归一化是你的差异化护城河，也是成本大头**：DSH 主流因为进程内所以免了协议层；你要跨 harness（Claude Code Anthropic + Codex Responses + Qwen Code Chat），协议归一化（9 种转换）是必须且无法外包的。可参考 `dsh-vision-recognizer` 的做法——VLM 调用统一走 OpenAI 兼容 + 支持 Anthropic 原生，降低 VLM 端协议复杂度。
3. **补一个"准入声明"设计点**：如果未来要支持 DSH 客户端（或任何有 inputModalities 类准入的 harness），只改 base_url 不够。可在 proxy 的"模型能力判定"（§6）里扩展一个"准入声明注入"能力，或在能力判定表里记录"该模型是否需要声明图片输入"。当前三 harness（CC/Codex/Qwen Code）没有此闸门，属预留。
4. **"结构化证据"是工具派的升级方向，可借鉴到描述 prompt**：DSH 工具派（ModLens/tool-vision）让 VLM 输出固定 JSON（OCR 全文/布局/坐标/uncertainty）。你的 Tier1/Tier2 描述 prompt 可以吸收"显式 uncertainty + 逐字 OCR"思想，提升纯文本追问时的可靠性（与你的追问检测 §5.8 互补）。
5. **上下文隔离是个免费加分项**：DSH 的 L5 子代理做"图不进主上下文，只回文字"——你的 proxy 天然如此（图在 IR 层就被替换），这是代理派的天然优势，可在文档里强调。
6. **免费/本地 VLM 通道值得默认支持**：DSH 生态几乎每个插件都默认 Ollama/免费档。你的 §6.3 `[vlm]` 已支持任意 OpenAI 兼容端点，建议把"本地 Ollama 自动探测"（参考 vision-recognizer / vision-proxy 的 `autoLocalOllama`）写进默认配置，降低用户上手成本。

---

## 6. 风险与开放问题

- **数据新鲜度**：本调研基于 awesome-dsh-plugin 2026-08-16 快照 + 各仓库当前 README。DSH 插件迭代极快（vision-router 已到 v1.3.0、工具动辄上百测试），个别实现细节可能已漂移；分类框架（层/范式）比插件个体更稳定。
- **"自动层是否真的自动"**：L3 适配器包装虽然自动，但**用户必须在模型选择器里选中新 provider 路由**（或设默认）才生效——不是"装了就全局生效"。vision-router 明确警告"不修改原模型组"。这意味着"无痕"仍有一层显式选择成本。
- **描述质量天花板**：多个插件承认"密集 UI 截图小字会丢"（vision-proxy README）。图→文字是有损的，结构化证据/坐标工具（toolkit）是补法之一。
- **权限与安全**：安装即运行第三方代码；多数插件把图片 base64 发往云 VLM（隐私提示都写了）。本地化（Ollama/macOS Vision/CDP 自家浏览器）是隐私取向的解法。

## 7. 参考资料

- [awesome-dsh-plugin（精选列表）](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)
- [DSH 官方 capability-seams 文档](https://github.com/deepseek-ai/deepseek-harness/blob/main/docs/capability-seams.md)
- 代表性插件仓库（见 §2 表格内链）：`dsh-vision-recognizer`、`dsh-deepseek-vision`、`dsh-llm-vision-bridge`、`dsh-vision-proxy`、`dsh-vision-mix`、`dsh-vision-guard`、`dsh-vision-toolkit`、`dsh-vision-router`、`modlens`、`dsh-tool-vision`、`dsh-vision-subagent`、`dsh-subagent-vision`、`dsh-plugin-image-input`、`dsh-plugin-multimodal`、`deepseek-vision`（MCP）、`free-vision-skill`
- 前置调研：[RESEARCH.md](../RESEARCH.md)（2026-08-12，Claude Code/Codex 生态）
- 关联设计：[Qwen-MM-Plugins proxy 设计稿](../docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md)
