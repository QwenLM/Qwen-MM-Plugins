# Qwen-MM-Plugins proxy - Phase 1 手工测试（精简版）

> 验证「给纯文本模型看图」的完整链路。所有请求都发到本地代理 `127.0.0.1:8787`，
> 用 `qwen-mm-plugins-proxy logs` 看每步是否 `injected:1`（图被转写）或 `stripped`（剥离）。

## 0. 前置
- 已安装 proxy CLI（`qwen-mm-plugins-proxy`）；代理可 `start`。
- `~/.qwen-mm-plugins/proxy.json` 已配好 relay（上游端点+key）与 `vlm`（视觉模型+key，key 打码不外露）。
- 准备一张测试图，如 `C:\Users\bunny\Downloads\test.png`。

## 1. 启动与健康检查
```powershell
qwen-mm-plugins-proxy start
qwen-mm-plugins-proxy check      # 无问题则 check ok；三处 harness base_url 应为 8787
```

## 2. 通用发送脚本（各用例改 path/model/内容即可）
```powershell
$IMG_B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\Users\bunny\Downloads\test.png"))
$body = @{ model="<模型>"; messages=@( @{ role="user"; content=@(
  @{ type="text"; text="这张图里有什么？" },
  @{ type="image_url"; image_url=@{ url="data:image/png;base64,$IMG_B64" } }
) } ) } | ConvertTo-Json -Depth 10
$r = Invoke-WebRequest -Uri "http://127.0.0.1:8787<路径>" -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
```

## 3. 用例 T1-T12（路径 + 模型 + 预期）
| 用例 | 路径 | 模型 | 预期（日志/响应） |
|---|---|---|---|
| T1 Anthropic 贴图 | `/v1/messages` | deepseek-v4-flash | `injected:1`，回复描述图中内容 |
| T2 Chat data URL 贴图 | `/v1/chat/completions` | deepseek-v4-flash | `injected:1`，无 base64 残留 |
| T3 Responses 贴图 | `/v1/responses` | deepseek-v4-pro | `injected:1`，回复正常 |
| T4 工具返回图（assistant tool_calls + tool 图） | `/v1/chat/completions` | deepseek-v4-pro | 不崩溃、`injected:1`、base64 不外泄 |
| T5 vision 模型直通 | `/v1/chat/completions` | minimax-m3（已标 vision） | `injected:0, stripped:0`（图原样透传） |
| T6 未知模型默认拦截 | `/v1/chat/completions` | 未登记模型 | 默认 text_only，走转写 |
| T7 fail-open（拔 VLM key） | 任一路径 | 任意 | HTTP 200 + 注入「看不到图…」，日志 `fail_open:"AUTH"`；`$env:QWEN_MM_PROXY_VLM_API_KEY="invalid-key"`（PowerShell 空串不生效） |
| T8 多图 [[图片K]] 前缀 | `/v1/chat/completions` | deepseek-v4-pro | 同条消息 2 图 → `injected:2`，注入带 `[[图片1]]/[[图片2]]` |
| T9 流式 | `/v1/chat/completions` + `stream:true` | deepseek-v4-pro | 返回 SSE `data: {...}` 块（Phase 1 同步转发）；body 用 `--data-binary @文件` 传 |
| T10 test-image | `qwen-mm-plugins-proxy test-image <图>` | VLM | 输出 Tier1(全面) 描述 |
| T11 生命周期 | `stop`/`status` | — | stop 还原接线、status 正常，不报 WinError |
| T12 relay 按模型路由 | 配置两条同协议不同 models 的 relay | deepseek-v4-flash / glm-5.1 | 各命中对应 relay |

## 4. 真实 harness 三终端（5.13）
拓扑=本代理第一层 A：`harness → 8787 → (CC Switch / Codex++ / 直连) → 供应商`。
- ① Claude Code + CC Switch：relay `{protocol:"anthropic", base_url:"http://127.0.0.1:15721", via:"cc-switch"}`，日志看 `proto:"anthropic"`。
- ② Codex + Codex++：relay `{protocol:"responses", base_url:"http://127.0.0.1:57321/v1", via:"codex-plus"}`，日志看 `proto:"responses"`。
- ③ 裸 Qwen Code：relay `{protocol:"chat", base_url:"<直连端点>"}`，日志看 `proto:"chat"`。
- 三台 harness 的 base_url 均指向 `http://127.0.0.1:8787`（`start` 自动接线/备份，`stop` 自动还原）。
- 每台贴图后 `qwen-mm-plugins-proxy logs` 应有对应 `proto` 且 `injected:1, upstream_status:200`。

## 5. 备注
- 工具（CC Switch/Codex++）切换 provider 会重写 harness base_url → 需重跑 `qwen-mm-plugins-proxy start` 指回 8787。
- 模型能力确认：`qwen-mm-plugins-proxy models`（显式改）、`models-scan`（草稿）；每次 start 只对**新模型**询问。

## 6. 常见问题
| 现象 | 排查 |
|---|---|
| 日志没有请求 | harness 没走 8787（工具抢占了 base_url） |
| `injected:0, fail_open:"CONTEXT_FULL"` | 上下文预算算满（大图/长文）；`_text_without_data_urls` 已排除 data URL |
| 上游 404 | relay base_url 版本段不对（anthropic 带 /v1；chat/responses 带 v3 等） |
| VLM 报错/看不到图 | `test-image` 单独验证 VLM key/端点；无 key 属预期 fail-open |
| 中文回复截断 | content-length 按 UTF-8 字节计算（已修）；确认代理为新代码 |
