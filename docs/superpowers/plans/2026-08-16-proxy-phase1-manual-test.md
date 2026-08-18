# Qwen-MM-Plugins proxy — Phase 1 手工测试（从 0 安装 + 完整用例，PowerShell 版）

> 目的：在真实环境人工验证 Phase 1 一阶段效果（对应 spec §10.5 的 8 项验收）。
> 本文件是从零开始的可执行指南：**安装 qwen-mm-plugins-proxy → 配置 relay + VLM → 启动 → 按 T1–T12 逐条验证**。
> 本机没有 Git Bash（只有 PowerShell / CMD），**全部命令已按 PowerShell 编写**，直接在 PowerShell 里复制粘贴即可。

---

## 0. 环境与准备清单（先看这个）

本机已验证状态：

| 项目               | 状态       | 说明                                                                                                      |
| ------------------ | ---------- | --------------------------------------------------------------------------------------------------------- |
| PowerShell         | ✅ 5.1     | 以下命令都在 PowerShell 里跑（Windows 自带）                                                              |
| uv / uvx           | ✅ 0.11.15 | D:\Program Files\Python314\Scripts\uv.exe                                                                 |
| Python             | ✅ 3.14.6  | 若依赖装失败，见 §1.3 降级到 3.12                                                                        |
| curl.exe           | ✅         | C:\Windows\system32\curl.exe（PowerShell 里**必须写 curl.exe**，裸 curl 是 Invoke-WebRequest 别名） |
| 本地 checkout      | ✅         | E:\LLMproject\Github\Qwen-MM-Plugins-plus（proxy-capability-spec 分支）                                   |
| 端口 8787 / 8788   | ✅ 空闲    | proxy 数据面 / 控制面                                                                                     |
| ~/.qwen-mm-plugins | ⬜ 未创建  | 本次安装会新建                                                                                            |

需要你准备的**凭据**（本机不存、不泄露，只写进本地配置文件）：

1. **主 relay（上游文本模型）**：DeepSeek key，或任意 Anthropic / Responses / OpenAI Chat 格式端点的 key。测试计划默认模型 deepseek-v4-pro，对应 DeepSeek。
2. **VLM（把图片转成文字）**：默认 qwen-vl-max 走 DashScope；或者任何 OpenAI 兼容端点上的**视觉**模型（base_url + 模型名 + key）。
3. **一张测试图片**（png/jpg 均可，建议带文字的截图）。下文统一用 C:\Users\bunny\Downloads\test.png，你替换成自己的路径。

> 你的 fork 分支未发布到 PyPI（proxy 是 0.1.0 新增 capability），所以**必须用本地 checkout 安装**，不能用 uvx --from qwen-mm-plugins[proxy]（那是发布后才能用的）。

> **路径写法（PowerShell）**：Windows 路径一律反斜杠，例如 C:\Users\bunny\Downloads\test.png。命令里图片路径建议用单引号包裹（PowerShell 单引号是字面量）：'C:\Users\bunny\Downloads\test.png'。下文所有命令已按此写好。

> **显示编码（每个 PowerShell 窗口开头执行一次即可，后续命令都生效）**：
>
> ```powershell
> [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
> ```
>
> 这样所有中文（请求/响应/日志）正常显示，不用每条命令重复贴。想让**以后每次开 PowerShell 都自动生效**，写入配置文件：
>
> ```powershell
> Add-Content $PROFILE '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8'
> ```
>
> 注：PowerShell 5.1 的 `Invoke-RestMethod` 会把中文响应解码成乱码，测试用例统一用 `Invoke-WebRequest` + UTF-8 解码（见 §5）；判据以 proxy 日志为准。

---

## 1. 安装 proxy CLI（两种方式，任选其一）

### 1.1 方式 A：装成全局命令（推荐）

在 PowerShell 里：

```powershell
cd E:\LLMproject\Github\Qwen-MM-Plugins-plus
uv tool install --editable E:\LLMproject\Github\Qwen-MM-Plugins-plus --with httpx
```

验证（开个**新**终端，让 PATH 生效）：

```powershell
qwen-mm-plugins-proxy --help
```

- 若提示找不到命令：uv 把 shim 装在 %USERPROFILE%\.local\bin（C:\Users\bunny\.local\bin）。把它加进 PATH 后开新终端：
  ```powershell
  # 方法 1：uv 自动帮你加 PATH
  uv tool update-shell
  # 方法 2：手动加（永久生效）
  [Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';' + "$env:USERPROFILE\.local\bin", 'User')
  ```

### 1.2 方式 B：项目内 uv run（免改 PATH）

不装成全局命令，每次命令前加 uv run --extra proxy，在 checkout 目录里执行：

```powershell
cd E:\LLMproject\Github\Qwen-MM-Plugins-plus
uv run --extra proxy qwen-mm-plugins-proxy --help
```

> 本指南其余部分统一写 qwen-mm-plugins-proxy ...，方式 B 的用户请自行在前面补 uv run --extra proxy。

### 1.3 若 Python 3.14 上依赖安装失败

```powershell
uv python install 3.12
uv tool install --editable E:\LLMproject\Github\Qwen-MM-Plugins-plus --with httpx --python 3.12
```

---

## 2. 配置 ~/.qwen-mm-plugins/proxy.json

配置文件位置：C:\Users\bunny\.qwen-mm-plugins\proxy.json（可用环境变量 QWEN_MM_CONFIG_DIR 覆盖）。

先建目录（PowerShell）：

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.qwen-mm-plugins" | Out-Null
```

把下面内容保存为 C:\Users\bunny\.qwen-mm-plugins\proxy.json（用记事本/VS Code 新建文件粘贴；**把 <...> 占位换成你的真实 key**；三个 relay 分别覆盖测试用的三种入站协议）：

```json
{
  "server": {
    "bind_host": "127.0.0.1",
    "bind_port": 8787,
    "ui_port": 8788
  },
  "relays": [
    {
      "name": "deepseek-chat",
      "protocol": "chat",
      "base_url": "https://api.deepseek.com",
      "api_key": "<DEEPSEEK_API_KEY>",
      "models": ["deepseek-*"]
    },
    {
      "name": "deepseek-anthropic",
      "protocol": "anthropic",
      "base_url": "https://api.deepseek.com/anthropic",
      "api_key": "<DEEPSEEK_API_KEY>",
      "models": ["deepseek-*"]
    },
    {
      "name": "deepseek-responses",
      "protocol": "responses",
      "base_url": "https://api.deepseek.com",
      "api_key": "<DEEPSEEK_API_KEY>",
      "models": ["deepseek-*"]
    }
  ],
  "vlm": {
    "model": "qwen-vl-max",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "<DASHSCOPE_API_KEY 或任意 OpenAI 兼容视觉端点 key>",
    "format": "chat",
    "cache_disk": false,
    "auto_local_ollama": true,
    "timeout_ms": 120000,
    "max_tokens": 4096
  },
  "model_capabilities": {}
}
```

**字段说明**：

- server：数据面 8787（curl 和 harness base_url 指向它）、控制面 8788（/status）。
- relays[]：按**入站协议**选上游。_select_relay 的匹配顺序：先 (协议, model 通配) → 再仅 协议 → 最后默认 relay（§6.3）。
  - protocol：anthropic | responses | chat，决定转发路径和鉴权头（anthropic 用 x-api-key，其余用 Authorization: Bearer）。**必填且强校验**——只能是这三个之一，写错/缺失时 check 会显式报错（config error: relay ... protocol must be one of ...），不再静默回退默认配置。
  - base_url：上游 API 的**完整根地址**。目标路径：anthropic → /messages、responses → /responses、chat → /chat/completions。**anthropic 协议固定补 /v1**（Anthropic 端点规范 /v1/messages，自动 /v1/v1 去重）——用户填直连根即可，如 .../api/coding 自动拼成 .../api/coding/v1/messages，无需手动加 /v1；chat / responses 按 Codex++ build_versioned_url 启发式：
    - base 已以 path 结尾 → 原样
    - base 以 # 结尾 → 跳过版本段，直接 base + path
    - base 最后段形如 v<数字>（v1/v3/v1beta）→ 直接 base + path
    - base 非纯 origin（scheme://host 后还有路径）→ 直接 base + path
    - base 纯 origin（scheme://host）→ base + /v1 + path
    - 最后 /v1/v1 去重
      例：https://api.deepseek.com（chat）→ /v1/chat/completions；火山 .../api/coding/v3（chat/responses）→ /v3/chat/completions、/v3/responses；anthropic 直连根 https://ark.cn-beijing.volces.com/api/coding → 自动拼 .../api/coding/v1/messages（无需手动加 /v1）。
  - models：通配符，匹配请求的 model 字段。留空则兜底该协议所有模型。
  - 若你的 DeepSeek key 不支持 Responses 协议（老账号），把 deepseek-responses 的 base_url 换成一个支持 Responses 的端点。
- vlm：图片转写的视觉后端，与主 relay 解耦。
  - 没 DashScope key 时：把 base_url 指向你的 OpenAI 兼容视觉端点、model 填该端点的视觉模型名即可（format 保持 chat）。
  - auto_local_ollama=true：若本地 11434 有 Ollama，会优先走本地视觉模型（图片不出本机）；当前本机未运行 Ollama，会静默回退到云端。
- model_capabilities：模型能力覆盖表，**默认留空 {}** 即可，内置名单已覆盖本测试所有模型：
  - deepseek/* → text_only（拦截剥图）→ 对应 T1–T4、T8、T9
  - qwen-vl-* → vision（直通不剥图）→ 对应 T5
  - 未知模型（如 mystery-model）→ 默认 text_only（拦截）→ 对应 T6
  - 需要自定义时在此加 "模型通配" : "vision" | "text_only"。

**env 覆盖（可选）**：QWEN_MM_PROXY_VLM_MODEL / QWEN_MM_PROXY_VLM_BASE_URL / QWEN_MM_PROXY_VLM_API_KEY / QWEN_MM_PROXY_VLM_FORMAT / QWEN_MM_PROXY_BIND_PORT，优先级 **proxy.json > env > 默认**。T7 会用到 QWEN_MM_PROXY_VLM_API_KEY 清空做 fail-open。

---

## 3. 启动 & 健康检查

```powershell
qwen-mm-plugins-proxy start      # 首次会打印 listening on 127.0.0.1:8787 (data) / 8788 (control)
qwen-mm-plugins-proxy status     # 预期：running (pid xxx)
qwen-mm-plugins-proxy check      # 预期：check ok；若 relay/VLM 没配会有 ⚠ 提示
```

控制面探活：

```powershell
curl.exe -s http://127.0.0.1:8787/status
# 预期：{"ok": true, "relays": 3, "vlm_model": "qwen-vl-max"}
```

看结构化日志（含 proxy_request / vl_call / vlm_cache_hit 埋点）：

```powershell
qwen-mm-plugins-proxy logs

---

## 4. 准备测试图片（生成 base64）

T1/T2/T3/T8 需要图片 base64。**推荐用真实图片**（描述更可验证）。在 PowerShell 里：

```powershell
# 把下面的路径换成你自己的图片（Windows 反斜杠路径，单引号包裹）
$IMG = 'C:\Users\bunny\Downloads\test.png'
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($IMG))
Write-Output ("base64 length: " + $IMG_B64.Length)   # 确认非空（正常图片几万到几十万）
```

> 也可以用任意一个 1×1 PNG 占位（合法 base64，VLM 会描述成"纯色/空白图"）：
> iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==
> 用法：$IMG_B64 = '上面那串'（在下面每个 T 的代码块里先运行）。

后面所有用例里，$IMG_B64 表示上面生成的变量；**每条用例单独开 PowerShell 窗口时记得先重新生成**。

---

## 5. 测试用例 T1–T12

> 前置：proxy 已在运行（§3）。所有请求打 127.0.0.1:8787（proxy 数据面），模型名透传、图片在**进入上游前**被替换。
> 每个用例分两步：先建 $body（PowerShell 对象 ConvertTo-Json），再 Invoke-WebRequest 发送 + UTF-8 解码显示（**不用 curl.exe -d**——它会把含 base64 的大 body 塞进命令行，超 Windows 32767 上限报「文件名或扩展名太长」）。直接整块复制粘贴即可。

**显示与判据**：下面各用例用 `Invoke-WebRequest` 发请求 + 手动 `[Text.Encoding]::UTF8` 解码响应，中文能正常显示（PowerShell 5.1 的 `Invoke-RestMethod` 会按错误编码解码中文导致乱码，故不用它）。功能判据以 proxy 日志为准（`qwen-mm-plugins-proxy logs` 里 `injected:1, upstream_status:200` 即通过）。

**图片安全网行为**：

- 所有图片（结构化块 + 字符串内嵌 data URL）都会被识别并转写/剥离；字符串 data URL 会在 IR 层**提前剥离为 [图片] 短占位**（base64 不占文本预算、不碰主模型文本），**普通网址 / 文件路径 / 非 image data URL 不受影响**。
- 工具返回图（read_image / 截图等被模型主动调用）同样处理（T4）。
- 历史有图 + 当前轮纯文本追问时，proxy 注入「描述未覆盖请重发图+问题，禁止编造」提示（spec §5.8）。

### T1. Anthropic 入站贴图（等价 Claude Code，§10.5 #1）

```powershell
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\test.png'))
$body = @{
  model = 'deepseek-v4-flash'
  messages = @(
    @{ role = 'user'; content = @(
      @{ type = 'text'; text = '这张图里有什么？' },
      @{ type = 'image'; source = @{ type = 'base64'; media_type = 'image/png'; data = $IMG_B64 } }
    ) }
  )
} | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/messages' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：HTTP 200（返回 JSON 内容）；上游收到图片被替换成 [图片描述] …（VLM 的 Tier2 聚焦描述，因为带用户问题），请求体无 base64, 残留；纯文本模型正常回答，不报图片错误。日志里 stripped:1, injected:1。

### T2. Chat 入站 data URL 贴图（Qwen Code，§10.5 #3）

```powershell
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\test.png'))
$body = @{
  model = 'deepseek-v4-pro'
  messages = @(
    @{ role = 'user'; content = @(
      @{ type = 'text'; text = '看图' },
      @{ type = 'image_url'; image_url = @{ url = "data:image/png;base64,$IMG_B64" } }
    ) }
  )
} | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/chat/completions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：HTTP 200；上游收到 [图片描述] …，无 base64, 残留。

### T3. Responses 入站（Codex，§10.5 #2）

```powershell
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\test.png'))
$body = @{
  model = 'deepseek-v4-pro'
  input = @(
    @{ role = 'user'; content = @(@{ type = 'input_text'; text = 'check' }) },
    @{ type = 'function_call_output'; call_id = 'c1'; output = "data:image/png;base64,$IMG_B64" }
  )
} | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/responses' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：HTTP 200；字符串内嵌 data URL 被抽出替换为 [图片]（短标记，避免重复注入），无 base64 残留进主模型上下文（§4.2.1 / §5.3）。

### T4. 工具返回图：结构化块 + 字符串 data URL（§10.5 #4）

```powershell
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\test.png'))
$body = @{
  model = 'deepseek-v4-pro'
  messages = @(
    @{ role = 'user'; content = '用工具看看这张图' },
    @{ role = 'assistant'; content = $null; tool_calls = @(
        @{ id = 't1'; type = 'function'; function = @{ name = 'view_image'; arguments = '{}' } }
    ) },
    @{ role = 'tool'; tool_call_id = 't1'; content = "data:image/png;base64,$IMG_B64" }
  )
} | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/chat/completions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：HTTP 200；字符串 data URL 被抽出替换为 [图片]，无 base64 残留。

### T5. vision 模型直通不剥图（§10.5 #7）

同 T2，但 model 换成 qwen-vl-max（内置名单 qwen-vl-* → vision）：

```powershell
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\test.png'))
$body = @{
  model = 'minimax-m3'
  messages = @(
    @{ role = 'user'; content = @(
      @{ type = 'text'; text = '看图' },
      @{ type = 'image_url'; image_url = @{ url = "data:image/png;base64,$IMG_B64" } }
    ) }
  )
} | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/chat/completions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：图片**原样透传**（base64 保留），不注入 [图片描述]（pipeline 对 vision 模型零开销直通）。日志无 stripped/injected。

### T6. 未知模型默认拦截（§10.5 #7）

同 T2，但 model 换成 mystery-model（不在内置名单 → 默认 text_only）：

```powershell
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\test.png'))
$body = @{
  model = 'mystery-model'
  messages = @(
    @{ role = 'user'; content = @(
      @{ type = 'text'; text = '看图' },
      @{ type = 'image_url'; image_url = @{ url = "data:image/png;base64,$IMG_B64" } }
    ) }
  )
} | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/chat/completions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：默认拦截（text_only）→ 走一次 VLM，图片被替换为 [图片描述] …。注意：mystery-model 会被 relay 匹配落到"仅协议匹配"或默认 relay（_select_relay 先 (model,protocol) 再仅 protocol），上游若拒绝该模型名会 4xx——这属于上游行为，**剥图逻辑本身**以日志 stripped/injected 和请求体无 base64 为准。

### T7. fail-open：拔 VLM key（§10.5 #5）

清空 VLM key 后重启，再跑 T1。

> ⚠️ PowerShell 注意：`$env:VAR = ''`（空字符串）等于**删除该变量**，空值不会传给子进程，
> proxy 会回落 proxy.json 里的**真实 key** → mimo 照常转写，fail-open 永远不触发（曾踩坑）。
> 在 PowerShell 上要真正让 VLM 失败，必须用**无效但非空**的 key，或直接改配置文件。

```powershell
qwen-mm-plugins-proxy stop
# 方式 1（推荐，PowerShell 下空串不生效）：用一个必然无效的非空 key 覆盖
$env:QWEN_MM_PROXY_VLM_API_KEY = 'invalid-key-for-failopen-test'
qwen-mm-plugins-proxy start
# 方式 2：直接编辑 C:\Users\bunny\.qwen-mm-plugins\proxy.json，把 vlm.api_key 置为空串/删掉，再 start
```

跑一次 T1 的代码块（带图片）。

**预期**：仍返回 HTTP 200（绝不 400 / 死锁）；图片被剥离 + 注入 看不到图：视觉模型调用失败（…），请更换多模态模型或检查 VLM 配置，不要编造内容。；**历史缓存命中项仍注入**（同一张图在 T1 描述过 → 缓存命中不受 key 影响）。日志 fail_open:"AUTH"（无效 key 触发 401）或 "VLM_FAILED"。

测试完记得恢复：移除 $env:QWEN_MM_PROXY_VLM_API_KEY（Remove-Item Env:\QWEN_MM_PROXY_VLM_API_KEY），或恢复 proxy.json 里的 api_key，再重启。

### T8. 多图 [[图片K]] 前缀（§5.6）

同一 user 消息带 2 张图：

```powershell
$IMG1 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\a.png'))
$IMG2 = [Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\Users\bunny\Downloads\b.png'))
$body = @{
  model = 'deepseek-v4-pro'
  messages = @(
    @{ role = 'user'; content = @(
      @{ type = 'text'; text = '对比这两张图' },
      @{ type = 'image_url'; image_url = @{ url = "data:image/png;base64,$IMG1" } },
      @{ type = 'image_url'; image_url = @{ url = "data:image/png;base64,$IMG2" } }
    ) }
  )
} | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/chat/completions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：上游收到两条 [图片描述]，分别带 [[图片1]] / [[图片2]] 前缀（target.index+1），无 base64 残留。

### T9. 流式（§10.5 #6）

```powershell
$body = @{ model = 'deepseek-v4-pro'; stream = $true; messages = @(@{ role = 'user'; content = 'hi' }) } | ConvertTo-Json -Depth 5
curl.exe -N -s http://127.0.0.1:8787/v1/chat/completions -H 'content-type: application/json' -d $body
```

**预期**：返回 SSE 格式（data: {...} 序列 → data: [DONE]）。Phase 1 为同步转发（httpx.Client.post 拿全量后原样回传），-N 只关缓冲，能清晰看到 SSE 事件块。

### T10. test-image（§8.3）

```powershell
qwen-mm-plugins-proxy test-image C:\Users\bunny\Downloads\a.png
qwen-mm-plugins-proxy test-image C:\Users\bunny\Downloads\a.png --question "绿色按钮左边的文字是什么"
```

**预期**：第一条输出 Tier1 (全面): …；第二条输出 Tier1 (全面): … + Tier2 (聚焦): … 并排对比。

### T11. 生命周期 + 回滚（§10.5 #8）

```powershell
qwen-mm-plugins-proxy stop
qwen-mm-plugins-proxy status   # 预期：not running
```

> 本测试只跑了 curl 直连，**没有**改写任何 harness 的 base_url，因此无 ~/.codex/config.toml / ~/.claude/settings.json 回滚负担（只有走 install.sh 装 harness 插件才会动它们）。

### T12. relay 按模型路由（spec §6.3，修复 6061b0c）

配两个同 protocol、不同 models 的 relay，验证按 model 通配命中对应 relay 的 base_url（而不是总选第一个）。修改 C:\Users\bunny\.qwen-mm-plugins\proxy.json 的 relays 为：

```json
"relays": [
  {
    "name": "chat-a",
    "protocol": "chat",
    "base_url": "<端点A>",
    "api_key": "<KEY_A>",
    "models": ["deepseek-*"]
  },
  {
    "name": "chat-b",
    "protocol": "chat",
    "base_url": "<端点B>",
    "api_key": "<KEY_B>",
    "models": ["other-*"]
  }
]
```

重启 proxy 后分别发：

```powershell
qwen-mm-plugins-proxy stop
qwen-mm-plugins-proxy start

# model=deepseek-v4-pro  → 应命中 chat-a
$body = @{ model = 'deepseek-v4-flash'; messages = @(@{ role = 'user'; content = 'hi' }) } | ConvertTo-Json -Depth 5
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/chat/completions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())

# model=other-model      → 应命中 chat-b（改一下 models 通配符与你测试名匹配）
$body = @{ model = 'glm-5.1'; messages = @(@{ role = 'user'; content = 'hi' }) } | ConvertTo-Json -Depth 5
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/v1/chat/completions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

**预期**：两个请求分别转发到对应 base_url（可用日志里的 proxy_request 事件核对，或把两个端点配成不同响应体以区分）。

---

## 5.13 进阶：真实 harness 端到端验证（spec §10.5 硬性验收）

> T1–T12 是 curl 直连代理（数据面）的等价模拟，验证 proxy 引擎。**spec §10.5 的「Phase 1 完成」硬性判定是在真实 harness 里贴图跑通**（Claude Code / Codex / Qwen Code）。
> **注意**：本步的 install.sh 是 bash 脚本，需要 bash。你机器上的 Git 自带 bash（D:\Program Files\Git\bin\bash.exe），可在 PowerShell 里这样调用它；若仍不可用，可跳过本步，仅用 T1–T12 验证 proxy 引擎。

```powershell
# 用 Git 自带的 bash 跑 install.sh（会同时改写 Claude Code / Codex / Qwen Code 三处 base_url 指向 127.0.0.1:8787）
& 'D:\Program Files\Git\bin\bash.exe' -c "cd /e/LLMproject/Github/Qwen-MM-Plugins-plus && bash install.sh local"
qwen-mm-plugins-proxy check    # 路由态识别，确认 base_url 已指向本地代理
```

预期：~/.claude/settings.json（ANTHROPIC_BASE_URL）与 ~/.codex/config.toml（model provider base_url）指向 http://127.0.0.1:8787，原配置备份为 *.qwen-mm-proxy.bak。

> 这就是"一次安装、多个 CLI 都生效"的原因：proxy 的安装 hook（proxy_rewrite_cc / proxy_rewrite_codex / proxy_rewrite_qwen_code）不区分 target harness，装上 proxy 就同时改写三处 base_url（spec §8.2）。

然后启动 proxy，在 Claude Code / Codex 里真实贴图验证（§10.5 #1/#2/#4），完成后 bash install.sh uninstall 回滚。
---

### 5.13.1 三终端真实贴图用例（拓扑 = 我们代理第一层 A：harness → 8787 → 工具 → 供应商）

> 以下三套用例把「真实 harness 里贴图、代理转写、日志验签」跑通，是 §10.5 的 Phase 1 硬性验收。
> **共同前提**：代理已装、VLM key 有效（先 `qwen-mm-plugins-proxy test-image C:\Users\bunny\Downloads\test.png` 确认 VLM 可用）；三台 harness 的 base_url 都必须指向 `http://127.0.0.1:8787`（第一跳）。
> 每个场景的 `.qwen-mm-plugins/proxy.json` 里各放一条对应 relay（可并存，按 protocol/models 区分）；`via` 仅用于 `check` 显示拓扑。

#### 通用校验步骤（三台通用）
```powershell
qwen-mm-plugins-proxy start
# 在 harness 里贴一张图（如 C:\Users\bunny\Downloads\test.png）并问：这张图里有什么？
qwen-mm-plugins-proxy logs   # 看是否出现 proto 对应的 proxy_request
qwen-mm-plugins-proxy check  # 看 relay 拓扑提示 + VLM/端口是否就绪
```
**通过标准**：日志出现 `injected:1、stripped:0、upstream_status:200`，且 harness 的回答能描述出图中细节（不是『看不到图』）。
**失败模式**：日志**没有**这条 proxy_request → harness 没走 8787（工具抢占了 base_url，需关掉该工具对 harness 的路由）；有请求但 `upstream_status` 非 200 → 看 base_url 拼接与工具端口是否监听。

#### 场景 1 · Claude Code + CC Switch（两层经 CC Switch，端口 15721）
```json
{ "name": "cc-claude", "protocol": "anthropic", "base_url": "http://127.0.0.1:15721", "via": "cc-switch", "models": ["*"] }
```
- 接线：Claude Code 的 ANTHROPIC_BASE_URL 指向 8787（install.sh proxy_rewrite_cc，或 CC Switch 里把 Claude 的 provider base_url 设成 http://127.0.0.1:8787）；确认 CC Switch 本地代理已监听 15721（它是我们的转发目标）。
- 日志看 `proto:"anthropic"` 的 proxy_request（我们经 CC Switch 转发到真实模型）。

#### 场景 2 · Codex + Codex++（两层经 Codex++，端口 57321）
```json
{ "name": "codex-plus", "protocol": "responses", "base_url": "http://127.0.0.1:57321/v1", "via": "codex-plus", "models": ["*"] }
```
- 接线：Codex 的 model provider base_url 指向 8787（install.sh proxy_rewrite_codex）；确认 Codex++ 本地协议代理已监听 57321。
- 日志看 `proto:"responses"` 的 proxy_request。（若你的 Codex 走 chat，protocol 改 `chat`、看 `proto:"chat"`。）

#### 场景 3 · 裸 Qwen Code（无工具，我们代理直连供应商）
```json
{ "name": "qwen-direct", "protocol": "chat", "base_url": "<Qwen Code 平时直连的端点>", "models": ["*"] }
```
- 接线：Qwen Code base_url 指向 8787（install.sh proxy_rewrite_qwen_code 写 ~/.qwen-code/.env 的 DASHSCOPE_BASE_URL=http://127.0.0.1:8787，或手动）。
- 日志看 `proto:"chat"` 的 proxy_request（我们 relay 直连上游，无工具层）。

#### 结束与回滚
三套都跑通后，`bash install.sh uninstall` 恢复三处原始 base_url（备份为 *.qwen-mm-proxy.bak）；或手动把 harness base_url 改回原值，并移除 proxy.json 里本次临时加的 relay。

#### 5.13.2 自动接线/回滚 与 首次模型能力确认

从本版起，`start`/`stop` 自带自动接线：(1) `start` 自动把三处 harness 的 base_url 指到本代理（先备份 `*.qwen-mm-proxy.bak`，幂等），并按 `routing.auto_wire` 激活 proxy.json 预置的 `relay_templates`；(2) `stop` 自动从备份还原 harness base_url 并移除本次激活的 relay。全程只动 `~/.qwen-mm-plugins/proxy.json` + 三处 base_url，**不写 CC Switch / Codex++ 自身配置**。

**首次启用路由必须显式确认模型看图能力**（有感、弹到脸上）：`start` 在未确认（`routing.capability_confirmed=false`）时进入交互引导，逐条列出扫描到的模型名（来源：relay_templates.models + 已有 model_capabilities + 三个 harness/工具的配置文件），↑/↓ 选、空格切换「支持图片」、回车完成；**未标的默认纯文本（text_only，最安全）**。确认后写入 `model_capabilities` + 置位，后续 start/stop 全自动。非交互终端不静默通过（会提示用 `qwen-mm-plugins-proxy models-scan` 复核）。

**协议识别自动**：`detect_protocol(path, body)` 按路径（`/v1/messages`=anthropic、`/v1/responses`=responses、`/v1/chat/completions`=chat）再按 body 结构兜底，用户不需要声明协议。

**CC Switch / Codex++ 切换配置的约定**：工具切 provider 会重写 harness 的 base_url（远离 8787），且不触发我们的 start/stop。此时需重跑 `qwen-mm-plugins-proxy start`（幂等，把 base_url 指回 8787），或用 `check` 看偏离告警。

---

## 6. 备注 / 已知记录

- 真实 harness 贴图路径 = T1/T2/T3 的 curl 等价；harness 无感知（模型名透传、base64 被代理替换）。
- 上下文预算为 Phase 1 简化版（128k 固定窗口 + bytes/2 粗估，plan 已记录）。
- check 双重剥图检测为端口探测占位（plan 已记录）。
- **SteamTools hosts 劫持**：本机 Steam++ 把 github.com/api.github.com 等劫持到 127.0.0.1，导致 schannel 报 SEC_E_NO_CREDENTIALS。本测试的上游（DeepSeek / DashScope）不受影响；只有操作 GitHub 相关时需用 git -c http.sslBackend=openssl -c http.sslVerify=false ...（详见 ~/.dsh/AGENTS.md）。

---

## 7. 常见问题排查

| 现象                                                | 排查                                                                                                                                                                              |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| qwen-mm-plugins-proxy: command not found            | 方式 B 用 uv run --extra proxy 前缀；或把 %USERPROFILE%\.local\bin 加 PATH 后开新终端（见 §1.1）                                                                                 |
| 报错「找不到路径 C:\e\...」                         | 说明你在用 Git Bash 的 /e/ 路径写法；本机用 PowerShell，路径写 E:\LLMproject\...（反斜杠）                                                                                        |
| curl 报「Invoke-WebRequest」或参数解析错误          | 你敲了 curl（别名）；PowerShell 里必须写 curl.exe                                                                                                                                 |
| port 8787 already in use                            | check 会提示；qwen-mm-plugins-proxy logs 看是否旧实例残留，stop 后重 start，或改 proxy.json 的 bind_port                                                                          |
| 上游返回 401                                        | relay 的 api_key 填错或该端点不支持所选 protocol（DeepSeek Anthropic 端点要用 https://api.deepseek.com/anthropic/v1）                                                             |
| 上游返回 404                                        | 多半是 base_url 版本段不对：chat/responses 用 .../v3（拼 .../v3/chat/completions、.../v3/responses），anthropic 必须带 v1（拼 .../v1/messages）；纯主机 https://host 会自动补 /v1 |
| VLM 报错 / 看不到图：…                             | test-image 单独验证 VLM 后端；检查 vlm.api_key / base_url / model 是否支持视觉；无 key 时属预期的 fail-open                                                                       |
| 装依赖报 Python 版本错误                            | 见 §1.3，uv python install 3.12 后用 --python 3.12 重装                                                                                                                          |
| config error: relay ... protocol must be one of ... | proxy.json 的 relays[] 里 protocol 写错或缺失；必须是 anthropic                                                                                                                   |

```

```
