# Qwen-MM-Plugins proxy — Phase 1 验收清单（spec §10.5）

> 状态：**已完成脚本化验证**（2026-08-17）。真实 harness（Claude Code / Codex / Qwen Code）本机无法自动验证的项标注 **待人工验证** 并给出验证命令。逐项对照 spec §10.5 的 8 项硬性判定。

## 环境与方法

- 分支：`proxy-capability-spec`
- Python：仓库 `.venv`（python3.12），pytest / ruff 走 `.venv/bin/*`
- 构建/安装：`bash install.sh local`（本地模式写绝对路径到 tracked manifests，用 `bash install.sh local --restore` 还原）
- 配置：`~/.qwen-mm-plugins/proxy.json`（0600），入口 `qwen-mm-plugins-proxy`
- 端口：数据面 127.0.0.1:8787 / 控制面 127.0.0.1:8788

## 验收项

### 1. Claude Code（Anthropic 入站）—— [ ] 通过 / [x] **待人工验证**

**验证命令（人工）：**

```bash
# 启动代理
qwen-mm-plugins-proxy start
qwen-mm-plugins-proxy status
# Claude Code 内贴一张图（含报错截图），确认：
#   - 收到注入 [图片描述] {desc} 的文本模型回答
#   - 会话不报错
qwen-mm-plugins-proxy logs | tail
```

**判定**：贴图 → 文本模型收到 `[图片描述]` 描述并正常回答，无 400/协议错误。本机无法驱动真实 Claude Code，待人工验证。

### 2. Codex（Responses 入站）—— [ ] 通过 / [x] **待人工验证**

**验证命令（人工）：**

```bash
# Codex 配置 base_url 指向代理（Task 13 install.sh 已接线，或手改 ~/.codex/config.toml）
# Codex 内贴图 / 触发 read_image、截图工具
qwen-mm-plugins-proxy logs | tail
```

**判定**：工具返回的图片被剥成文字；请求上下文无 base64 残留。本机无法驱动真实 Codex，待人工验证。

### 3. Qwen Code（OpenAI Chat 入站）—— [ ] 通过 / [x] **待人工验证**

**验证命令（人工）：**

```bash
# Qwen Code 配置 DASHSCOPE_BASE_URL 指向代理（Task 13 写入 ~/.qwen-code/.env）
# Qwen Code 内贴图
qwen-mm-plugins-proxy logs | tail
```

**判定**：同上，OpenAI Chat 入站图片被替换为描述，无 base64。本机无法驱动真实 Qwen Code，待人工验证。

### 4. 工具返回图（结构化块 + 字符串内嵌 data URL）—— [x] 通过（脚本化验证）

**脚本化验证（已跑，2026-08-17）：**

```bash
.venv/bin/python -m pytest tests/test_proxy_integration.py tests/test_proxy_pipeline.py -q
# 12 passed in 1.73s
```

**判定**：`test_responses_inbound_function_output_data_url_stripped`、`test_nested_tool_result_image_injected`、`test_text_embedded_data_url_replaced_no_base64_residue` 全绿，覆盖结构化块与字符串内嵌 data URL 两种形态，均注入描述并去除 base64 残留。

### 5. fail-open（拔 VLM key / 断网）—— [x] 通过（脚本化验证）

**脚本化验证（已跑，2026-08-17）：**

```bash
.venv/bin/python -m pytest tests/test_proxy_pipeline.py::test_fail_open_on_vlm_error -q
# 1 passed in 0.08s
```

**人工验证（推荐）**：`proxy.json` 移除 `vlm.api_key` 且关闭 auto_local_ollama，贴图仍能对话，得到「看不到图：视觉模型调用失败」提示，绝不 400 死锁。

### 6. 流式（同协议直通 + Anthropic ↔ Chat 翻译）—— [x] 通过（脚本化验证）

**脚本化验证（已跑，2026-08-17）：**

```bash
.venv/bin/python -m pytest tests/test_proxy_stream.py -q
# 3 passed in 0.01s
```

**人工验证**：三 harness 各自开启流式，回答正常流式返回（真实 harness 项待人工验证）。

### 7. 能力判定（vision 直通 / 未知默认拦截）—— [x] 通过（脚本化验证）

**脚本化验证（已跑，2026-08-17）：**

```bash
.venv/bin/python -m pytest tests/test_proxy_capability.py tests/test_proxy_pipeline.py::test_vision_model_passthrough_no_pipeline -q
# 4 passed in 0.08s
```

**判定**：qwen-vl 等 vision 模型直通不剥图；未知模型默认拦截走一次 VLM。

### 8. 生命周期 + uninstall 回滚—— [x] 通过（脚本化验证）

**脚本化验证（已跑，2026-08-17）：**

```bash
.venv/bin/python -m pytest tests/test_proxy_cli.py tests/test_proxy_install.py -q
# 35 passed in 1.01s

.venv/bin/python -m qwen_mm_plugins_proxy check
# ⚠ no relays configured  (无 relay 配置时告警，退出码 1 属预期；配置后为 check ok)
```

**人工验证**：`start/stop/status/logs/test-image/check` 逐个可用（真实进程生命周期）；`bash install.sh uninstall` 恢复三 harness 原 base_url（`*.qwen-mm-proxy.bak` 还原）——待人工验证。

## 结论

- 脚本化可验项（4/5/6/7/8 的测试部分）：**全部通过**（12 + 1 + 3 + 4 + 35 = 55 项相关用例全绿；全量离线回归 450 passed / 7 skipped / 6 deselected）。
- 真实 harness 项（1/2/3 全部、4/6/8 的人工部分）：**待人工验证**（本机无法驱动真实 Claude Code / Codex / Qwen Code，验证命令已给出）。
- 最终判定：**Phase 1 代码与脚本化验收通过**；真实 harness 端到端验收待人工执行 spec §10.5 的 1/2/3 项。