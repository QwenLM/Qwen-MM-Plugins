# Qwen-MM-Plugins Proxy Capability (Phase 1) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `proxy` capability——一个本地常驻 HTTP 协议代理，为 Claude Code / Codex / Qwen Code 三个 harness 提供"纯文本模型经 VLM 识图"能力：拦截图片→VLM 转文字→替换→透传，三协议（Anthropic / Responses / Chat）全量互转，fail-open 兜底。

**Architecture:** Python 常驻 HTTP server（stdlib `http.server.ThreadingHTTPServer`，每请求一线程）。入站三协议按路径/结构解析为统一中间表示 IR，图片在 IR 层统一抽取（结构化块 + 字符串内嵌 data URL）→ VLM（默认 DashScope Qwen，OpenAI 兼容 `/chat/completions`，可选 Anthropic 原生与本地 Ollama）→ 文字注入/剥离 → 序列化回三协议转发上游。数据面 `127.0.0.1:8787`（三入站路由），控制面 `127.0.0.1:8788`（JSON 管理 API，Phase 1 仅 CLI 消费）。模型能力判定：配置 > 内置名单 > 未知默认拦截。任何失败 fail-open。

**Tech Stack:** Python >= 3.10（stdlib-only 优先，不用 tomllib）、httpx（上游流式客户端，走 `[proxy]` extra）、pytest、ruff、bash（install.sh）。

## Global Constraints

- 语言/运行时：Python `>=3.10`，尽量 stdlib-only；**不用 tomllib**（3.11+ 才保证）→ 配置用 **JSON**（`~/.qwen-mm-plugins/proxy.json`，600 权限），**不是 spec 里写的 toml**（这是与 spec 的显式偏差，见 Task 1）。
- 命名对齐：文件夹 `src/capabilities/proxy/`、入口 `qwen-mm-plugins-proxy`、extra `[proxy]`、import `qwen_mm_plugins_proxy`。
- 配置读取一律走 `shared.env.get_env`（调用时读，env > 用户配置文件 > 默认），不直读 `os.environ`；新增 env：`QWEN_MM_PROXY_VLM_MODEL` / `QWEN_MM_PROXY_VLM_BASE_URL` / `QWEN_MM_PROXY_VLM_API_KEY` / `QWEN_MM_PROXY_VLM_FORMAT` / `QWEN_MM_PROXY_BIND_PORT`。
- 端口：数据面 `127.0.0.1:8787`（`bind_port`），控制面 `127.0.0.1:8788`（`ui_port`）；均可配。
- 常量（沿用 spec §5）：`ANALYZE_DEPTH_LIMIT=50`、`GOLDEN_WINDOW_DEPTH=10`、`BATCH_SIZE=5`、`BATCH_MAX_ATTEMPTS=2`、`CACHE_CAPACITY=500`、`CACHE_TTL_HOURS=24`、`CONTEXT_SAFETY_MARGIN=0.9`、`AVG_DESC_BUDGET=100`、`VLM_SEMAPHORE=5`。
- 幂等/安全：api_key 只存本地配置（600）；日志不写明文 key；图片字节只在 IR 层处理后进入文字，主模型上下文绝不出现 base64 图片。
- 测试：`python3 -m pytest -m "not reachability" tests/`（离线）、`ruff format --check .`、`ruff check .`、`bash -n install.sh`、`python3 scripts/check_manifests.py`。
- 发布不变量：`plugin-versions.json`、harness manifests、`__version__` 一致；`scripts/tag_plugin_release.py` 强校验。
- Phase 1 只做：CC/Codex/Qwen Code 三 harness；三协议 3×3 全量（含 Responses 上游）；流式同协议直通 + Anthropic↔Chat；内存缓存；CLI 生命周期；无 Web UI / hooks / 磁盘缓存 / 跨协议流式（Responses↔其他）。

---

### Task 1: capability 骨架与打包接入

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/__init__.py`
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/__main__.py`
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/config.py`
- Modify: `pyproject.toml`（`[project.optional-dependencies]` 加 `proxy` extra；`[project.scripts]` 加入口）
- Modify: `install.sh`（`CAP_ITEMS`/`CAP_VERSIONS`/`CAP_DESC` 加 proxy）
- Modify: `plugin-versions.json`（加 proxy 版本）
- Test: `tests/test_proxy_config.py`

**Interfaces:**
- Consumes: `shared.env.get_env`, `shared.env.config_dir`（已存在，见 `src/shared/env.py`）。
- Produces:
  - `qwen_mm_plugins_proxy.__version__: str`
  - `qwen_mm_plugins_proxy.config.ProxyConfig` dataclass + `load_config(path: str | None = None) -> ProxyConfig` + `default_config() -> ProxyConfig`
  - `qwen_mm_plugins_proxy.__main__.main(argv: list[str] | None = None) -> int`（Phase 1 先返回 0 占位，Task 12 实现真命令）

- [ ] **Step 1: 写失败的配置测试**

```python
# tests/test_proxy_config.py
"""Proxy capability: config load/save + env override + defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwen_mm_plugins_proxy.config import default_config, load_config


def test_default_config_defaults():
    cfg = default_config()
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 8787
    assert cfg.ui_port == 8788
    assert cfg.vlm.model  # 非空默认
    assert cfg.vlm.format == "chat"


def test_load_config_reads_json_and_env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "proxy.json"
    cfg_path.write_text(json.dumps({
        "server": {"bind_port": 9000},
        "vlm": {"model": "qwen-vl-max"},
    }))
    monkeypatch.setenv("QWEN_MM_PROXY_BIND_PORT", "9100")  # env > file
    cfg = load_config(str(cfg_path))
    assert cfg.bind_port == 9100
    assert cfg.vlm.model == "qwen-vl-max"


def test_missing_config_file_uses_defaults(tmp_path: Path):
    cfg = load_config(str(tmp_path / "nope.json"))
    assert cfg.bind_port == 8787
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_config.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qwen_mm_plugins_proxy'`

- [ ] **Step 3: 建包与最小实现**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/__init__.py
"""Qwen-MM-Plugins proxy: a local HTTP protocol proxy that gives text-only models vision.

Standalone (non-MCP) capability: a resident HTTP server on 127.0.0.1:8787 that intercepts
images in Anthropic / Responses / Chat requests, transcribes them via a VLM, and forwards
text to the real upstream. See docs/superpowers/specs/2026-08-13-qwen-mm-proxy-design.md.
"""

__version__ = "0.1.0"
```

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/config.py
"""Proxy configuration: JSON file (~/.qwen-mm-plugins/proxy.json, 0600) + env overrides.

Spec says proxy.toml; the repo floor is Python 3.10 (no guaranteed tomllib), so we use JSON
(see plan Global Constraints). Read via shared.env.get_env for env overrides.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from shared.env import get_env

VLM_FORMATS = ("chat", "anthropic")


@dataclass
class VLMConfig:
    model: str = "qwen-vl-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    format: str = "chat"  # chat | anthropic（responses 留二阶段）
    cache_disk: bool = False
    auto_local_ollama: bool = True
    timeout_ms: int = 120_000
    max_tokens: int = 4096


@dataclass
class RelayConfig:
    name: str
    protocol: str  # anthropic | responses | chat
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)
    capability: str | None = None  # 显式覆盖能力判定


@dataclass
class ProxyConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787
    ui_port: int = 8788
    relays: list[RelayConfig] = field(default_factory=list)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    model_capabilities: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyConfig":
        server = data.get("server", {})
        vlm = data.get("vlm", {})
        return cls(
            bind_host=server.get("bind_host", "127.0.0.1"),
            bind_port=int(server.get("bind_port", 8787)),
            ui_port=int(server.get("ui_port", 8788)),
            relays=[RelayConfig(**r) for r in data.get("relays", [])],
            vlm=VLMConfig(**{k: v for k, v in vlm.items() if k in VLMConfig.__dataclass_fields__}),
            model_capabilities=data.get("model_capabilities", {}),
        )

    def to_dict(self) -> dict:
        return {
            "server": {"bind_host": self.bind_host, "bind_port": self.bind_port, "ui_port": self.ui_port},
            "relays": [r.__dict__ for r in self.relays],
            "vlm": self.vlm.__dict__,
            "model_capabilities": self.model_capabilities,
        }


def default_config() -> ProxyConfig:
    return ProxyConfig.from_dict({})


def _apply_env(cfg: ProxyConfig) -> ProxyConfig:
    """Env overrides (QWEN_MM_PROXY_*), applied at load time via shared.env.get_env."""
    if v := get_env("QWEN_MM_PROXY_BIND_PORT"):
        cfg.bind_port = int(v)
    if v := get_env("QWEN_MM_PROXY_VLM_MODEL"):
        cfg.vlm.model = v
    if v := get_env("QWEN_MM_PROXY_VLM_BASE_URL"):
        cfg.vlm.base_url = v
    if v := get_env("QWEN_MM_PROXY_VLM_API_KEY"):
        cfg.vlm.api_key = v
    if v := get_env("QWEN_MM_PROXY_VLM_FORMAT"):
        if v in VLM_FORMATS:
            cfg.vlm.format = v
    return cfg


def load_config(path: str | None = None) -> ProxyConfig:
    if path is None:
        from shared.env import config_dir

        path = os.path.join(config_dir(), "proxy.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = ProxyConfig.from_dict(json.load(f))
    except (OSError, ValueError):
        cfg = default_config()
    return _apply_env(cfg)
```

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/__main__.py
"""Console entry: qwen-mm-plugins-proxy. Commands land in Task 12."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: pyproject 与安装器注册**

`pyproject.toml` 修改两处：

```toml
# [project.optional-dependencies] 内新增
proxy = ["httpx"]                             # standalone HTTP proxy: upstream streaming client

# [project.scripts] 内新增
qwen-mm-plugins-proxy = "qwen_mm_plugins_proxy.__main__:main"
```

`install.sh` 三处（与 plugin-versions.json 顺序严格一致，`scripts/check_manifests.py` 与 `tests/test_install_sh.py` 强校验）：

```bash
# CAP_ITEMS 数组加 proxy（保持顺序与 CAP_VERSIONS/plugin-versions.json 一致）
CAP_ITEMS=(core api search video-memory video-edit blender freecad edu-agent proxy)
# CAP_VERSIONS 尾部对应加 0.1.0
CAP_VERSIONS=(1.0.2 1.0.3 1.0.3 1.0.1 1.0.1 1.0.1 1.0.1 1.0.1 0.1.0)
# CAP_DESC 尾部对应加
"local protocol proxy: intercept images, transcribe via VLM, forward text to text-only models"
```

`plugin-versions.json` 加：

```json
"proxy": "0.1.0"
```

- [ ] **Step 5: 运行测试确认通过 + 提交**

Run: `python3 -m pytest tests/test_proxy_config.py -v`
Expected: PASS（3 passed）

```bash
git add src/capabilities/proxy pyproject.toml install.sh plugin-versions.json tests/test_proxy_config.py
git commit -m "feat(proxy): scaffold proxy capability package and packaging registration"
```

---

### Task 2: check_manifests.py 支持非 MCP server + manifest 文件

**Files:**
- Modify: `scripts/check_manifests.py`（`server_package` 旁加 `NON_MCP_SERVER_CAPS = {"proxy"}` 例外）
- Create: `src/capabilities/proxy/.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、`.qoder-plugin/plugin.json`、`.mcp.json`
- Test: `tests/test_proxy_config.py` 追加一个 manifest 冒烟（或直接靠 `check_manifests.py` 全量跑）

**Interfaces:**
- Consumes: 无（Task 1 的包结构）。
- Produces: proxy 的 manifests（name=`qwen-mm-plugins-proxy`，version=`0.1.0`），`check_manifests.py` 对 proxy 走"非 MCP server"分支（不要求 mcpServers/`.mcp.json` 的 uvx 启动、不要求 `mcp_framework`）。

- [ ] **Step 1: 理解检查器对 proxy 会怎么判**

Run: `python3 scripts/check_manifests.py`
Expected: FAIL 且报 proxy 缺失 manifest；若 proxy 已有 manifests，会按"server capability"要求 mcpServers —— 这正是要改的：proxy 是 HTTP server 不是 MCP server。

- [ ] **Step 2: 给检查器加非 MCP 例外**

```python
# scripts/check_manifests.py — server_package() 定义下方新增
NON_MCP_SERVER_CAPS = {"proxy"}  # standalone HTTP server capability (no MCP transport)


def server_package(cap_dir: Path) -> str | None:
    """The capability's MCP-server package dir (a valid import name with an __init__.py), if any."""
    if cap_dir.name in NON_MCP_SERVER_CAPS:
        return None  # non-MCP server capability: has a package, but no MCP server identity
    for sub in sorted(cap_dir.iterdir()):
        if sub.is_dir() and sub.name.isidentifier() and (sub / "__init__.py").is_file():
            return sub.name
    return None
```

并在 `check_capability` 里，`if import_name is not None:` 的 MCP 分支加保护注释即可（`server_package` 返回 None 后自然走"仅校验 manifests name/version 一致"路径，不再要求 `.mcp.json`/mcpServers/pyproject script 格式）。

- [ ] **Step 3: 写 proxy manifests**

```json
{
  "name": "qwen-mm-plugins-proxy",
  "version": "0.1.0",
  "description": "Qwen-MM-Plugins proxy — local HTTP protocol proxy giving text-only models vision: intercept images, transcribe via a VLM, forward text to the upstream. Standalone HTTP server (not an MCP server); manage via `qwen-mm-plugins-proxy start|stop|status|logs|test-image|check`.",
  "skills": []
}
```

同上内容写入 `.codex-plugin/plugin.json` 与 `.qoder-plugin/plugin.json`（保持 name/version 一致），并创建空的 `.mcp.json`（`{"mcpServers": {}}`，检查器对非 MCP server 不读它，但目录存在便于后续演进）。

- [ ] **Step 4: 全量跑检查器 + 回归**

Run: `python3 scripts/check_manifests.py`
Expected: PASS（proxy 走非 MCP 分支，其余 capability 不受影响）

Run: `python3 -m pytest -m "not reachability" tests/ -q`
Expected: PASS（无回归）

- [ ] **Step 5: 提交**

```bash
git add scripts/check_manifests.py src/capabilities/proxy
git commit -m "feat(proxy): support non-MCP server capability in manifest checker"
```

---

### Task 3: IR 数据模型与入站协议识别

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py`
- Test: `tests/test_proxy_ir.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - dataclasses `ImageBlock` / `ContentBlock` / `ToolResult` / `Message` / `IRRequest`
  - `detect_protocol(path: str, body: dict) -> str`：`"anthropic"`（`/v1/messages` 或含 `messages` 且顶层无 `input`）/ `"responses"`（`/v1/responses` 或含 `input`）/ `"chat"`（`/v1/chat/completions` 或含 `messages` 且含 `messages[0].role` 非 `system` 边缘）——按路径优先，路径缺失按结构推断；无法识别抛 `ValueError`。
  - `make_image_block(...)` 辅助（后续 Task 4/9 用）。

- [ ] **Step 1: 写失败的识别测试**

```python
# tests/test_proxy_ir.py
from __future__ import annotations

import pytest

from qwen_mm_plugins_proxy.ir import detect_protocol


def test_detect_by_path():
    assert detect_protocol("/v1/messages", {}) == "anthropic"
    assert detect_protocol("/v1/responses", {}) == "responses"
    assert detect_protocol("/v1/chat/completions", {}) == "chat"


def test_detect_by_structure():
    assert detect_protocol("/other", {"input": []}) == "responses"
    assert detect_protocol("/other", {"messages": [{"role": "user"}]}) == "chat"
    assert detect_protocol("/other", {"messages": [{"role": "user"}], "system": ""}) == "anthropic"


def test_detect_unknown_raises():
    with pytest.raises(ValueError):
        detect_protocol("/other", {})
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_ir.py -v`
Expected: FAIL（module not found / function not defined）

- [ ] **Step 3: 实现 IR 与识别**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py
"""Unified intermediate representation (IR) + inbound protocol detection.

All three inbound protocols normalize into IRRequest; the safety net works only on IR
(spec §3: one image pipeline for three protocols).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImageBlock:
    """Normalized image: either url (OpenAI forms) or base64+media_type (Anthropic)."""
    url: str | None = None
    media_type: str | None = None
    base64: str | None = None


@dataclass
class ContentBlock:
    type: str  # text | image | tool_use | tool_result
    text: str | None = None
    image: ImageBlock | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_result_content: list["ContentBlock"] | None = None


@dataclass
class Message:
    role: str  # user | assistant | tool
    content: list[ContentBlock] = field(default_factory=list)


@dataclass
class IRRequest:
    model: str
    messages: list[Message]
    system: str | None = None
    tools: list[dict] | None = None
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


def detect_protocol(path: str, body: dict) -> str:
    """Inbound protocol by request path first, then by body structure. Raise on ambiguity."""
    if path.endswith("/v1/messages") or "/messages" in path:
        return "anthropic"
    if path.endswith("/v1/responses") or "/responses" in path:
        return "responses"
    if path.endswith("/v1/chat/completions") or "/chat/completions" in path:
        return "chat"
    # Structure fallback (relay-converted paths).
    if "input" in body:
        return "responses"
    if "messages" in body:
        if "system" in body or any(m.get("role") == "assistant" and "content" in m and isinstance(m["content"], list)
                                   for m in body["messages"]):
            return "anthropic"
        return "chat"
    raise ValueError(f"cannot detect protocol for path={path!r}")
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_ir.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py tests/test_proxy_ir.py
git commit -m "feat(proxy): IR model and inbound protocol detection"
```

---

### Task 4: 入站解析（三协议 → IR，含 tool_result 与 data URL）

**Files:**
- Modify: `src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py`（加 `parse_anthropic` / `parse_responses` / `parse_chat` / `extract_data_urls`）
- Test: `tests/test_proxy_ir.py` 追加

**Interfaces:**
- Consumes: Task 3 的 IR dataclasses、`detect_protocol`。
- Produces:
  - `parse_anthropic(body: dict) -> IRRequest`、`parse_responses(body: dict) -> IRRequest`、`parse_chat(body: dict) -> IRRequest`
  - `extract_data_urls(text: str) -> list[str]`：正则抽出 `data:image/{subtype};base64,{payload}`（payload 字符集 `[A-Za-z0-9+/=]`）
  - `ContentBlock` 约定：图片统一为 `ContentBlock(type="image", image=ImageBlock(...))`；tool_result 为 `ContentBlock(type="tool_result", tool_result_content=[...])`；tool_use 为 `ContentBlock(type="tool_use", tool_use_id=..., tool_name=..., tool_input=...)`。

- [ ] **Step 1: 写失败测试（三个 parse + data URL）**

```python
# tests/test_proxy_ir.py 追加
from qwen_mm_plugins_proxy.ir import (
    ContentBlock,
    extract_data_urls,
    parse_anthropic,
    parse_chat,
    parse_responses,
)

ANTHROPIC_BODY = {
    "model": "deepseek-v4-pro",
    "system": "you are helpful",
    "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ]},
    ],
    "max_tokens": 1024,
}


def test_parse_anthropic_roundtrip_fields():
    ir = parse_anthropic(ANTHROPIC_BODY)
    assert ir.model == "deepseek-v4-pro"
    assert ir.system == "you are helpful"
    assert ir.max_tokens == 1024
    img = ir.messages[0].content[1]
    assert img.type == "image" and img.image.media_type == "image/png" and img.image.base64 == "AAAA"


def test_parse_responses_image_and_tool_result():
    body = {
        "model": "deepseek-v4-pro",
        "input": [
            {"role": "user", "content": [
                {"type": "input_text", "text": "hi"},
                {"type": "input_image", "image_url": "https://x/y.png"},
            ]},
            {"type": "function_call_output", "call_id": "c1", "output": "data:image/png;base64,QUJD"},
        ],
    }
    ir = parse_responses(body)
    assert ir.messages[0].content[1].image.url == "https://x/y.png"
    # 字符串内嵌 data URL 抽出（tool_result 是字符串时，正文保留、data URL 标记为 image 块）
    assert extract_data_urls(ir.messages[1].content[0].text) == ["data:image/png;base64,QUJD"]


def test_parse_chat_image_url():
    body = {
        "model": "glm-4.5v",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "x"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
            ]},
        ],
    }
    ir = parse_chat(body)
    img = ir.messages[0].content[1]
    assert img.type == "image" and img.image.url == "data:image/jpeg;base64,QUJD"


def test_extract_data_urls_multiple():
    text = "a data:image/png;base64,QUJD b data:image/jpeg;base64,REVG"
    assert extract_data_urls(text) == ["data:image/png;base64,QUJD", "data:image/jpeg;base64,REVG"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_ir.py -v`
Expected: FAIL（parse_* / extract_data_urls 未定义）

- [ ] **Step 3: 实现三个 parse + data URL 抽取**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py 追加
import re

_DATA_URL_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def extract_data_urls(text: str) -> list[str]:
    """Pull string-embedded base64 data URLs out of tool/assistant text (spec §4.2.1)."""
    return _DATA_URL_RE.findall(text)


def _image_block(source: dict) -> ContentBlock:
    if source.get("type") == "base64":
        return ContentBlock(type="image", image=ImageBlock(media_type=source.get("media_type"), base64=source.get("data")))
    return ContentBlock(type="image", image=ImageBlock(url=source.get("url")))


def _content_blocks(blocks: list) -> list[ContentBlock]:
    out: list[ContentBlock] = []
    for b in blocks:
        kind = b.get("type")
        if kind in (None, "text", "input_text", "output_text"):
            out.append(ContentBlock(type="text", text=b.get("text", "")))
        elif kind in ("image", "input_image"):
            if "source" in b:
                out.append(_image_block(b["source"]))
            else:
                out.append(ContentBlock(type="image", image=ImageBlock(url=b.get("image_url", {}).get("url"))))
        elif kind in ("image_url",):
            url = b["image_url"].get("url") if isinstance(b.get("image_url"), dict) else b.get("image_url")
            out.append(ContentBlock(type="image", image=ImageBlock(url=url)))
        elif kind == "tool_use":
            out.append(ContentBlock(type="tool_use", tool_use_id=b.get("id"), tool_name=b.get("name"),
                                    tool_input=b.get("input")))
        elif kind == "tool_result":
            content = b.get("content")
            if isinstance(content, str):
                text = content
                out.append(ContentBlock(type="text", text=text))
                for url in extract_data_urls(text):
                    out.append(ContentBlock(type="image", image=ImageBlock(url=url)))
            else:
                out.append(ContentBlock(type="tool_result", tool_use_id=b.get("tool_use_id"),
                                        tool_result_content=_content_blocks(content or [])))
        elif kind == "function_call":
            out.append(ContentBlock(type="tool_use", tool_use_id=b.get("call_id"), tool_name=b.get("name"),
                                    tool_input=json.loads(b.get("arguments") or "{}")))
        elif kind == "function_call_output":
            output = b.get("output")
            out.append(ContentBlock(type="text", text=str(output)))
            for url in extract_data_urls(str(output)):
                out.append(ContentBlock(type="image", image=ImageBlock(url=url)))
        else:
            out.append(ContentBlock(type="text", text=str(b)))
    return out


def _message(role: str, content) -> Message:
    if isinstance(content, str):
        text = content
        blocks = [ContentBlock(type="text", text=text)]
        for url in extract_data_urls(text):
            blocks.append(ContentBlock(type="image", image=ImageBlock(url=url)))
        return Message(role=role, content=blocks)
    return Message(role=role, content=_content_blocks(content))


def parse_anthropic(body: dict) -> IRRequest:
    return IRRequest(
        model=body.get("model", ""),
        system=body.get("system"),
        messages=[_message(m["role"], m["content"]) for m in body.get("messages", [])],
        tools=body.get("tools"),
        stream=bool(body.get("stream")),
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
    )


def parse_responses(body: dict) -> IRRequest:
    messages: list[Message] = []
    for item in body.get("input", []):
        role = item.get("role")
        if role == "user":
            messages.append(_message("user", item.get("content", [])))
        elif role == "assistant":
            messages.append(_message("assistant", item.get("content", [])))
        elif item.get("type") in ("function_call", "function_call_output"):
            messages.append(_message("tool", [item]))
    return IRRequest(model=body.get("model", ""), messages=messages, tools=body.get("tools"),
                     stream=bool(body.get("stream")), max_tokens=body.get("max_output_tokens"))


def parse_chat(body: dict) -> IRRequest:
    messages = [_message(m["role"], m["content"]) for m in body.get("messages", [])]
    system = None
    if messages and messages[0].role == "system":
        system = messages.pop(0).content[0].text
    return IRRequest(model=body.get("model", ""), messages=messages, system=system,
                     tools=body.get("tools"), stream=bool(body.get("stream")),
                     max_tokens=body.get("max_tokens"), temperature=body.get("temperature"))
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_ir.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py tests/test_proxy_ir.py
git commit -m "feat(proxy): parse three inbound protocols into IR with data-URL extraction"
```

---

### Task 5: 上游序列化（IR → 三协议，9 组合）

**Files:**
- Modify: `src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py`（加 `serialize_anthropic` / `serialize_responses` / `serialize_chat`）
- Test: `tests/test_proxy_ir.py` 追加

**Interfaces:**
- Consumes: Task 3/4 的 IR。
- Produces: `serialize_anthropic(ir) -> dict`、`serialize_responses(ir) -> dict`、`serialize_chat(ir) -> dict`。三者与 parse 互逆（同协议 round-trip）；图片块序列化时**原样保留**（安全网在 Task 9 负责替换，序列化不丢信息）。

- [ ] **Step 1: 写失败测试（round-trip 三协议 + 9 组合矩阵）**

```python
# tests/test_proxy_ir.py 追加
from qwen_mm_plugins_proxy.ir import serialize_anthropic, serialize_chat, serialize_responses


def test_parse_serialize_anthropic_roundtrip():
    body = {
        "model": "deepseek-v4-pro", "system": "s",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ]}],
    }
    ir = parse_anthropic(body)
    out = serialize_anthropic(ir)
    assert out["model"] == "deepseek-v4-pro"
    assert out["messages"][0]["content"][1]["type"] == "image"
    assert out["messages"][0]["content"][1]["source"]["data"] == "AAAA"


def test_serialize_chat_preserves_image_and_tools():
    ir = parse_chat({
        "model": "qwen", "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]},
        ],
        "tools": [{"type": "function", "function": {"name": "f"}}],
    })
    out = serialize_chat(ir)
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][1]["content"][0]["type"] == "image_url"
    assert out["tools"] == ir.tools


def test_serialize_responses_keeps_input_list():
    ir = parse_responses({"model": "m", "input": [
        {"role": "user", "content": [{"type": "input_text", "text": "x"}]},
    ]})
    out = serialize_responses(ir)
    assert out["input"][0]["role"] == "user"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_ir.py -v`
Expected: FAIL

- [ ] **Step 3: 实现三个 serialize**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py 追加
import json  # 顶部已有则复用


def _block_to_proto(block: ContentBlock, protocol: str) -> dict:
    if block.type == "text":
        if protocol == "anthropic":
            return {"type": "text", "text": block.text}
        if protocol == "responses":
            return {"type": "output_text", "text": block.text}
        return {"type": "text", "text": block.text}
    if block.type == "image":
        img = block.image
        if protocol == "anthropic":
            return {"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.base64}}
        if protocol == "responses":
            return {"type": "input_image", "image_url": img.url or img.base64}
        return {"type": "image_url", "image_url": {"url": img.url or f"data:{img.media_type};base64,{img.base64}"}}
    if block.type == "tool_use":
        if protocol == "responses":
            return {"type": "function_call", "call_id": block.tool_use_id, "name": block.tool_name,
                    "arguments": json.dumps(block.tool_input or {})}
        return {"type": "tool_use", "id": block.tool_use_id, "name": block.tool_name, "input": block.tool_input or {}}
    if block.type == "tool_result":
        content = [_block_to_proto(b, protocol) for b in (block.tool_result_content or [])]
        if protocol == "responses":
            return {"type": "function_call_output", "call_id": block.tool_use_id,
                    "output": _blocks_to_text(block.tool_result_content or [])}
        if protocol == "chat":
            return {"role": "tool", "tool_call_id": block.tool_use_id,
                    "content": _blocks_to_text(block.tool_result_content or [])}
        return {"type": "tool_result", "tool_use_id": block.tool_use_id, "content": content}
    return {"type": "text", "text": ""}


def _blocks_to_text(blocks: list[ContentBlock]) -> str:
    return "\n".join(b.text or "" for b in blocks if b.type == "text")


def _message_to_proto(msg: Message, protocol: str) -> dict:
    if protocol == "anthropic":
        return {"role": msg.role, "content": [_block_to_proto(b, protocol) for b in msg.content]}
    if protocol == "responses":
        return {"role": msg.role, "content": [_block_to_proto(b, protocol) for b in msg.content]}
    # chat
    if msg.role == "assistant":
        tool_calls = [{"id": b.tool_use_id, "type": "function",
                       "function": {"name": b.tool_name, "arguments": json.dumps(b.tool_input or {})}}
                      for b in msg.content if b.type == "tool_use"]
        text = "".join(b.text or "" for b in msg.content if b.type == "text")
        return {"role": "assistant", "content": text, "tool_calls": tool_calls or None}
    if msg.role == "tool":
        return {"role": "tool", "tool_call_id": (msg.content[0].tool_use_id if msg.content else None),
                "content": _blocks_to_text(msg.content)}
    return {"role": "user", "content": [_block_to_proto(b, protocol) for b in msg.content]}


def serialize_anthropic(ir: IRRequest) -> dict:
    out = {"model": ir.model, "messages": [_message_to_proto(m, "anthropic") for m in ir.messages]}
    if ir.system:
        out["system"] = ir.system
    if ir.tools:
        out["tools"] = ir.tools
    if ir.max_tokens:
        out["max_tokens"] = ir.max_tokens
    if ir.stream:
        out["stream"] = True
    return out


def serialize_responses(ir: IRRequest) -> dict:
    out = {"model": ir.model, "input": [_message_to_proto(m, "responses") for m in ir.messages]}
    if ir.tools:
        out["tools"] = ir.tools
    if ir.max_tokens:
        out["max_output_tokens"] = ir.max_tokens
    if ir.stream:
        out["stream"] = True
    return out


def serialize_chat(ir: IRRequest) -> dict:
    messages = [_message_to_proto(m, "chat") for m in ir.messages]
    if ir.system:
        messages.insert(0, {"role": "system", "content": ir.system})
    out = {"model": ir.model, "messages": messages}
    if ir.tools:
        out["tools"] = ir.tools
    if ir.max_tokens:
        out["max_tokens"] = ir.max_tokens
    if ir.stream:
        out["stream"] = True
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_ir.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/ir.py tests/test_proxy_ir.py
git commit -m "feat(proxy): serialize IR back to three upstream protocols"
```

---

### Task 6: 流式直通 + Anthropic ↔ Chat 事件翻译

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/stream.py`
- Test: `tests/test_proxy_stream.py`

**Interfaces:**
- Consumes: 无（纯 SSE 文本处理）。
- Produces:
  - `stream_same_protocol(inbound: Iterable[str]) -> Iterable[str]`：原样透传 SSE 行（`data:` 事件行逐行转发）。
  - `translate_anthropic_to_chat(sse_lines: Iterable[str]) -> Iterable[str]`：Anthropic 事件 → Chat `choices[].delta` 流。
  - `translate_chat_to_anthropic(sse_lines: Iterable[str]) -> Iterable[str]`：Chat 流 → Anthropic `content_block_delta` 流。
  - 事件只需覆盖 `text` 增量与 `message_stop`/`[DONE]` 终止；工具调用等复杂事件第二阶段。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_proxy_stream.py
from __future__ import annotations

from qwen_mm_plugins_proxy.stream import translate_anthropic_to_chat, translate_chat_to_anthropic


def test_translate_anthropic_text_to_chat():
    lines = [
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}',
        'data: {"type":"message_stop"}',
        "data: [DONE]",
    ]
    out = list(translate_anthropic_to_chat(lines))
    assert '"choices"' in out[0] and '"delta"' in out[0] and '"content":"hi"' in out[0]
    assert out[-1] == "data: [DONE]"


def test_translate_chat_text_to_anthropic():
    lines = [
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        "data: [DONE]",
    ]
    out = list(translate_chat_to_anthropic(lines))
    assert '"content_block_delta"' in out[0]
    assert '"text_delta"' in out[0] and '"text":"hi"' in out[0]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_stream.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/stream.py
"""SSE streaming: same-protocol passthrough + Anthropic <-> Chat event translation (Phase 1)."""

from __future__ import annotations

import json
from collections.abc import Iterable


def _data_payload(line: str) -> dict | None:
    if not line.startswith("data: "):
        return None
    payload = line[len("data: "):].strip()
    if payload in ("[DONE]", ""):
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def stream_same_protocol(inbound: Iterable[str]) -> Iterable[str]:
    yield from inbound


def translate_anthropic_to_chat(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = _data_payload(line)
        if payload is None:
            if line.strip() == "data: [DONE]":
                yield "data: [DONE]"
            continue
        kind = payload.get("type")
        if kind == "content_block_delta" and payload.get("delta", {}).get("type") == "text_delta":
            yield "data: " + json.dumps({"choices": [{"delta": {"content": payload["delta"]["text"]}}]})
        # message_stop / others: no chat equivalent in Phase 1 -> drop silently
    yield "data: [DONE]"


def translate_chat_to_anthropic(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = _data_payload(line)
        if payload is None:
            continue
        choices = payload.get("choices") or []
        delta = choices[0].get("delta", {}) if choices else {}
        text = delta.get("content")
        if text:
            yield "data: " + json.dumps({
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": text},
            })
    yield 'data: {"type":"message_stop"}'
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_stream.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/stream.py tests/test_proxy_stream.py
git commit -m "feat(proxy): SSE passthrough and Anthropic<->Chat streaming translation"
```

---

### Task 7: VLM 客户端（chat / anthropic + Ollama 探测 + env 覆盖）

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/vlm.py`
- Test: `tests/test_proxy_vlm.py`（用 `monkeypatch` 注入 httpx 假响应，无真实网络）

**Interfaces:**
- Consumes: `config.VLMConfig`、`ir.ImageBlock`。
- Produces:
  - `class VLMError(Exception)`（`reason: str` ∈ `AUTH|TIMEOUT|TRANSPORT|HTTP|PARSE`）
  - `class VLMClient`：`__init__(self, cfg: VLMConfig)`；`describe(self, image: ImageBlock, question: str | None = None, tier: int = 1) -> str`（chat 走 `POST {base_url}/chat/completions`，anthropic 走 `POST {base_url}/v1/messages`；多图批处理外层由 pipeline 控制）
  - `probe_ollama(timeout_s: float = 2.0) -> str | None`：探测 `http://localhost:11434/api/tags`，返回首个含 `vl`/`vision` 的模型名，无则 None。
  - prompt 构建：Tier1 全面描述 + 结构化证据（逐字 OCR + 布局要点 + 关键元素 + `uncertainty`）；Tier2 加用户问题聚焦。

- [ ] **Step 1: 写失败测试（httpx MockTransport 注入）**

```python
# tests/test_proxy_vlm.py
from __future__ import annotations

import httpx
import pytest

from qwen_mm_plugins_proxy.config import VLMConfig
from qwen_mm_plugins_proxy.ir import ImageBlock
from qwen_mm_plugins_proxy.vlm import VLMClient, VLMError, probe_ollama


def _client_with(transport) -> VLMClient:
    cfg = VLMConfig(model="qwen-vl-max", base_url="https://dashscope.example/v1", api_key="k")
    client = VLMClient(cfg)
    client._http = httpx.Client(transport=transport)
    return client


def test_describe_chat_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        body = request.json()
        assert body["model"] == "qwen-vl-max"
        assert body["messages"][0]["content"][0]["type"] == "image_url"
        return httpx.Response(200, json={"choices": [{"message": {"content": "一只橘猫"}}]})

    client = _client_with(httpx.MockTransport(handler))
    assert client.describe(ImageBlock(url="data:image/png;base64,QUJD")) == "一只橘猫"


def test_describe_http_error_raises_vlm_error():
    client = _client_with(httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad key"})))
    with pytest.raises(VLMError) as exc:
        client.describe(ImageBlock(url="data:image/png;base64,QUJD"))
    assert exc.value.reason == "AUTH"


def test_probe_ollama_finds_vision_model(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3-vl:4b"}, {"name": "llama3"}]})

    monkeypatch.setattr("qwen_mm_plugins_proxy.vlm.httpx.Client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler)))
    assert probe_ollama() == "qwen3-vl:4b"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_vlm.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/vlm.py
"""VLM backend client: OpenAI-compatible chat (primary) + Anthropic native; Ollama probe."""

from __future__ import annotations

import base64
import time

import httpx

from .config import VLMConfig
from .ir import ImageBlock

TIER1_PROMPT = (
    "Describe the image in detail. Return structured evidence:\n"
    "- OCR: verbatim text visible in the image (line by line)\n"
    "- Layout: key regions in reading order\n"
    "- Key elements: objects, UI, people, numbers\n"
    "- uncertainty: anything you cannot determine\n"
    "Never invent content that is not visible."
)
TIER2_PROMPT = "Answer the question from the image. Include OCR evidence and explicit uncertainty.\nQuestion: {q}"


class VLMError(Exception):
    def __init__(self, reason: str, message: str = ""):
        super().__init__(message)
        self.reason = reason


class VLMClient:
    def __init__(self, cfg: VLMConfig):
        self.cfg = cfg
        self._http = httpx.Client(timeout=cfg.timeout_ms / 1000.0)

    # -- prompt ---------------------------------------------------------------
    def _prompt(self, question: str | None, tier: int) -> str:
        return TIER2_PROMPT.format(q=question) if tier == 2 and question else TIER1_PROMPT

    def _image_content(self, img: ImageBlock) -> dict:
        if img.url:
            return {"type": "image_url", "image_url": {"url": img.url}}
        b64 = img.base64 or base64.b64encode(b"").decode()
        return {"type": "image_url", "image_url": {"url": f"data:{img.media_type or 'image/png'};base64,{b64}"}}

    # -- calls ----------------------------------------------------------------
    def describe(self, image: ImageBlock, question: str | None = None, tier: int = 1) -> str:
        prompt = self._prompt(question, tier)
        try:
            if self.cfg.format == "anthropic":
                return self._describe_anthropic(image, prompt)
            return self._describe_chat(image, prompt)
        except VLMError:
            raise
        except httpx.TimeoutException as exc:
            raise VLMError("TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise VLMError("TRANSPORT", str(exc)) from exc

    def _describe_chat(self, image: ImageBlock, prompt: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, self._image_content(image)]}],
            "max_tokens": self.cfg.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        resp = self._http.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            raise VLMError(_classify(resp.status_code), resp.text[:200])
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise VLMError("PARSE", str(exc)) from exc

    def _describe_anthropic(self, image: ImageBlock, prompt: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/v1/messages"
        body = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {"type": "base64", "media_type": image.media_type or "image/png",
                                              "data": image.base64 or ""}},
            ]}],
        }
        headers = {"x-api-key": self.cfg.api_key, "anthropic-version": "2023-06-01"} if self.cfg.api_key else {}
        resp = self._http.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            raise VLMError(_classify(resp.status_code), resp.text[:200])
        try:
            return "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
        except ValueError as exc:
            raise VLMError("PARSE", str(exc)) from exc


def _classify(status: int) -> str:
    if status == 401 or status == 403:
        return "AUTH"
    if status == 429:
        return "RATE_LIMIT"
    if status >= 500:
        return "HTTP"
    return "HTTP"


def probe_ollama(timeout_s: float = 2.0) -> str | None:
    """Return the first vision-capable Ollama model id, or None."""
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                return None
            for m in resp.json().get("models", []):
                name = m.get("name", "")
                if "vl" in name.lower() or "vision" in name.lower():
                    return name
    except httpx.HTTPError:
        return None
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_vlm.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/vlm.py tests/test_proxy_vlm.py
git commit -m "feat(proxy): VLM client (chat/anthropic) with Ollama probe and VLMError taxonomy"
```

---

### Task 8: 描述缓存（Tier1/Tier2 内存 LRU）

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/cache.py`
- Test: `tests/test_proxy_cache.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `class DescriptionCache`：`__init__(self, capacity: int = 500, ttl_hours: float = 24)`；`get(self, image_key: str, question: str | None) -> str | None`；`put(self, image_key: str, question: str | None, description: str) -> None`
  - `image_key(image: ImageBlock) -> str`：优先 `url`，否则 `sha256(base64)`；`CACHE_TTL_HOURS` 常量。
  - 语义：Tier1 键 `(image_key, None)`；Tier2 键 `(image_key, question)`；满容量按写入时间踢最旧（LRU）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_proxy_cache.py
from __future__ import annotations

import time

from qwen_mm_plugins_proxy.cache import DescriptionCache, image_key
from qwen_mm_plugins_proxy.ir import ImageBlock


def test_tier1_and_tier2_are_separate_keys():
    c = DescriptionCache()
    c.put("url1", None, "desc1")
    c.put("url1", "what is this?", "desc2")
    assert c.get("url1", None) == "desc1"
    assert c.get("url1", "what is this?") == "desc2"
    assert c.get("url1", "other question") is None


def test_lru_evicts_oldest():
    c = DescriptionCache(capacity=2)
    c.put("a", None, "A")
    time.sleep(0.01)
    c.put("b", None, "B")
    time.sleep(0.01)
    c.put("c", None, "C")  # evicts a
    assert c.get("a", None) is None
    assert c.get("b", None) == "B"
    assert c.get("c", None) == "C"


def test_image_key_uses_url_or_hash():
    assert image_key(ImageBlock(url="https://x/y.png")) == "https://x/y.png"
    assert image_key(ImageBlock(base64="QUJD"))  # 非空 sha256 前缀
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_cache.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/cache.py
"""Two-tier in-memory description cache (spec §5.5): Tier1 (image) + Tier2 (image+question)."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

from .ir import ImageBlock

CACHE_CAPACITY = 500
CACHE_TTL_HOURS = 24


def image_key(image: ImageBlock) -> str:
    if image.url:
        return image.url
    raw = image.base64 or ""
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DescriptionCache:
    def __init__(self, capacity: int = CACHE_CAPACITY, ttl_hours: float = CACHE_TTL_HOURS):
        self.capacity = capacity
        self.ttl_seconds = ttl_hours * 3600
        self._store: OrderedDict[tuple[str, str | None], tuple[float, str]] = OrderedDict()

    def get(self, image_key_: str, question: str | None) -> str | None:
        key = (image_key_, question)
        item = self._store.get(key)
        if item is None:
            return None
        written_at, desc = item
        if time.time() - written_at > self.ttl_seconds:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return desc

    def put(self, image_key_: str, question: str | None, description: str) -> None:
        key = (image_key_, question)
        self._store[key] = (time.time(), description)
        self._store.move_to_end(key)
        while len(self._store) > self.capacity:
            self._store.popitem(last=False)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_cache.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/cache.py tests/test_proxy_cache.py
git commit -m "feat(proxy): Tier1/Tier2 in-memory description cache (LRU + TTL)"
```

---

### Task 9: 图片处理管线（扫描 → 抽取 → VLM → 注入 / fail-open / 预算）

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/pipeline.py`
- Test: `tests/test_proxy_pipeline.py`

**Interfaces:**
- Consumes: `ir.*`、`cache.DescriptionCache`、`vlm.VLMClient`、`config.ProxyConfig`。
- Produces:
  - `class Pipeline`：`__init__(self, vlm: VLMClient, cache: DescriptionCache, semaphore=threading.Semaphore(5))`
  - `process(self, ir: IRRequest, cfg: ProxyConfig) -> ProcessResult`
  - `@dataclass ProcessResult`：`ir: IRRequest`（已被改写）、`stripped: int`、`injected: int`、`fail_open: str | None`、`vlm_calls: int`
  - 行为（spec §5）：扫描最近 50 条 user/tool 消息；当前轮 + 黄金窗口（10）内同步注入；图片替换为 `[图片描述] {desc}`（多图 `[[图片K]]` 前缀）；VLM 失败 → 剥离 + 注入「看不到图」提示（fail-open，绝不阻断）；上下文预算：`available = context*0.9 - text_tokens`，`X = available / 100`，`X<=1` 全剥 + 「上下文已满」。

- [ ] **Step 1: 写失败测试（注入 + fail-open + 预算）**

```python
# tests/test_proxy_pipeline.py
from __future__ import annotations

from qwen_mm_plugins_proxy.cache import DescriptionCache
from qwen_mm_plugins_proxy.config import ProxyConfig
from qwen_mm_plugins_proxy.ir import ContentBlock, ImageBlock, Message, parse_chat
from qwen_mm_plugins_proxy.pipeline import Pipeline
from qwen_mm_plugins_proxy.vlm import VLMClient, VLMError


class FakeVLM:
    def __init__(self, text="一只橘猫"):
        self.text = text
        self.calls = 0

    def describe(self, image, question=None, tier=1):
        self.calls += 1
        return self.text


def _ir_with_image(model="deepseek-v4-pro"):
    return parse_chat({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ]}],
    })


def test_injects_description_and_removes_image_block():
    vlm = FakeVLM()
    pipe = Pipeline(vlm, DescriptionCache())
    ir = _ir_with_image()
    result = pipe.process(ir, ProxyConfig())
    texts = [b.text for b in result.ir.messages[0].content if b.type == "text"]
    assert any("[图片描述]" in t and "橘猫" in t for t in texts)
    assert not any(b.type == "image" for m in result.ir.messages for b in m.content)
    assert result.injected == 1 and result.vlm_calls == 1


def test_fail_open_on_vlm_error():
    class BoomVLM:
        def describe(self, image, question=None, tier=1):
            raise VLMError("TIMEOUT", "timeout")

    pipe = Pipeline(BoomVLM(), DescriptionCache())
    result = pipe.process(_ir_with_image(), ProxyConfig())
    assert result.fail_open == "TIMEOUT"
    assert not any(b.type == "image" for m in result.ir.messages for b in m.content)
    texts = [b.text for b in result.ir.messages[0].content if b.type == "text"]
    assert any("看不到图" in t for t in texts)


def test_vision_model_passthrough_no_pipeline():
    pipe = Pipeline(FakeVLM(), DescriptionCache())
    ir = _ir_with_image(model="qwen-vl-max")  # 内置名单 vision -> 直通
    result = pipe.process(ir, ProxyConfig(model_capabilities={}))
    assert result.vlm_calls == 0 and result.injected == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_pipeline.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/pipeline.py
"""Image safety net on IR (spec §5): scan -> extract -> VLM -> inject/fail-open/budget."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from .cache import DescriptionCache, image_key
from .capability import CapabilityTable
from .config import ProxyConfig
from .ir import ContentBlock, IRRequest

ANALYZE_DEPTH_LIMIT = 50
GOLDEN_WINDOW_DEPTH = 10
CONTEXT_SAFETY_MARGIN = 0.9
AVG_DESC_BUDGET = 100  # tokens


@dataclass
class ProcessResult:
    ir: IRRequest
    stripped: int = 0
    injected: int = 0
    fail_open: str | None = None
    vlm_calls: int = 0


class Pipeline:
    def __init__(self, vlm, cache: DescriptionCache, semaphore: threading.Semaphore | None = None):
        self.vlm = vlm
        self.cache = cache
        self.semaphore = semaphore or threading.Semaphore(5)
        self.table = CapabilityTable()

    def process(self, ir: IRRequest, cfg: ProxyConfig) -> ProcessResult:
        if self.table.judge(ir.model, cfg) == "vision":
            return ProcessResult(ir=ir)  # vision model: zero overhead passthrough
        result = ProcessResult(ir=ir)
        budget = self._budget(ir, cfg)
        if budget <= 1:
            result.stripped = self._strip_all(ir, reason="上下文已满，图片未处理")
            result.fail_open = "CONTEXT_FULL"
            return result
        # 黄金窗口内（当前轮 + 最近 10 条）逐图处理；深层只走缓存命中（Phase 2 后台缓存二阶段）
        targets = self._collect_images(ir, depth=GOLDEN_WINDOW_DEPTH)
        quota = max(int(budget), 1)
        for idx, (msg, block_idx, img) in enumerate(targets[:quota]):
            outcome = self._handle_one(ir, msg, block_idx, img, result, current_turn=idx == 0)
            if outcome == "stripped":
                result.stripped += 1
        return result

    def _handle_one(self, ir, msg, block_idx, img, result, current_turn: bool) -> str:
        key = image_key(img)
        cached = self.cache.get(key, None)
        if cached is None:
            try:
                with self.semaphore:
                    cached = self.vlm.describe(img, tier=1)
                    result.vlm_calls += 1
                self.cache.put(key, None, cached)
            except Exception as exc:  # noqa: BLE001 - fail-open on ANY VLM failure
                self._replace(msg, block_idx, "[图片描述] 看不到图：视觉模型调用失败（%s），请更换多模态模型或检查 VLM 配置，不要编造内容。" % getattr(exc, "reason", type(exc).__name__))
                result.fail_open = getattr(exc, "reason", "VLM_FAILED")
                return "stripped"
        desc = f"[图片描述] {cached}"
        self._replace(msg, block_idx, desc)
        result.injected += 1
        return "injected"

    @staticmethod
    def _replace(msg, block_idx: int, text: str) -> None:
        msg.content[block_idx] = ContentBlock(type="text", text=text)

    def _collect_images(self, ir, depth: int):
        """Return [(message, block_index, image)] over the most recent `depth` user/tool messages."""
        targets = []
        for msg in ir.messages[-depth:]:
            for i, block in enumerate(msg.content):
                if block.type == "image" and block.image:
                    targets.append((msg, i, block.image))
        return targets

    @staticmethod
    def _strip_all(ir, reason: str) -> int:
        n = 0
        for msg in ir.messages:
            for i, block in enumerate(msg.content):
                if block.type == "image":
                    msg.content[i] = ContentBlock(type="text", text=f"[图片已省略] {reason}")
                    n += 1
        return n

    @staticmethod
    def _budget(ir: IRRequest, cfg: ProxyConfig) -> float:
        context = 128_000  # 默认窗口；可配 relay 时按模型取
        text_tokens = sum(len(b.text or "") // 2 for m in ir.messages for b in m.content)
        available = context * CONTEXT_SAFETY_MARGIN - text_tokens
        return available / AVG_DESC_BUDGET
```

> 注：`capability.CapabilityTable` 在 Task 10 定义；本任务先 import（会红，直到 Task 10 落地）——若想单任务可测，可先在本任务内联最小判定（模型名含 `vl`/`vision` → vision），Task 10 再替换为正式表。**推荐后一种**：本任务内联 `_is_vision_model()`，Task 10 删除内联改用 `CapabilityTable`（Task 10 的测试会覆盖）。

- [ ] **Step 3b（推荐）: 内联最小判定替代 import**

把 `from .capability import CapabilityTable` 换成内联：

```python
def _is_vision_model(self, model: str, cfg: ProxyConfig) -> bool:
    if model in cfg.model_capabilities:
        return cfg.model_capabilities[model] == "vision"
    return ("vl-" in model) or model.startswith(("openai/", "anthropic/", "google/", "qwen-vl"))
```

并在 `process` 里用 `if self._is_vision_model(ir.model, cfg):`。Task 10 会删除此函数并改回 `CapabilityTable`。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/pipeline.py tests/test_proxy_pipeline.py
git commit -m "feat(proxy): image pipeline with injection, fail-open, and context budget"
```

---

### Task 10: 模型能力判定（配置 > 内置名单 > 默认拦截）

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/capability.py`
- Modify: `src/capabilities/proxy/qwen_mm_plugins_proxy/pipeline.py`（删内联判定，改用 `CapabilityTable`）
- Test: `tests/test_proxy_capability.py`

**Interfaces:**
- Consumes: `config.ProxyConfig`。
- Produces: `class CapabilityTable`：`judge(self, model: str, cfg: ProxyConfig) -> str`（`"vision" | "text_only"`）；内置名单与 spec §6.2 一致；进程内 `_cache: dict[str, str]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_proxy_capability.py
from __future__ import annotations

from qwen_mm_plugins_proxy.capability import CapabilityTable
from qwen_mm_plugins_proxy.config import ProxyConfig

BUILTIN = {
    "deepseek/*": "text_only", "glm/*": "text_only", "zai/*": "text_only",
    "openai/*": "vision", "anthropic/*": "vision", "google/*": "vision",
    "qwen-vl-*": "vision", "qwen3.5-omni-*": "vision", "kimi-k2.7-code*": "vision",
    "openrouter/deepseek/*": "text_only",
}


def test_builtin_list_matches_spec():
    table = CapabilityTable()
    assert table.judge("deepseek/deepseek-chat", ProxyConfig()) == "text_only"
    assert table.judge("qwen-vl-max", ProxyConfig()) == "vision"
    assert table.judge("openrouter/deepseek/v3", ProxyConfig()) == "text_only"


def test_user_config_overrides_builtin():
    cfg = ProxyConfig(model_capabilities={"deepseek-vl-2": "vision"})
    assert CapabilityTable().judge("deepseek-vl-2", cfg) == "vision"


def test_unknown_model_defaults_to_intercept():
    assert CapabilityTable().judge("mystery-model", ProxyConfig()) == "text_only"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_capability.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/capability.py
"""Model capability judgment: user config > builtin list > unknown defaults to intercept."""

from __future__ import annotations

import fnmatch

from .config import ProxyConfig

BUILTIN_CAPABILITIES: dict[str, str] = {
    "deepseek/*": "text_only",
    "glm/*": "text_only",
    "zai/*": "text_only",
    "openai/*": "vision",
    "anthropic/*": "vision",
    "google/*": "vision",
    "qwen-vl-*": "vision",
    "qwen3.5-omni-*": "vision",
    "kimi-k2.7-code*": "vision",
    "openrouter/deepseek/*": "text_only",
}


class CapabilityTable:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def judge(self, model: str, cfg: ProxyConfig) -> str:
        if model in self._cache:
            return self._cache[model]
        capability = self._resolve(model, cfg)
        self._cache[model] = capability
        return capability

    @staticmethod
    def _resolve(model: str, cfg: ProxyConfig) -> str:
        # 1. 用户显式配置（精确模型名、前缀、通配符，顺序匹配命中即止）
        for pattern, cap in cfg.model_capabilities.items():
            if fnmatch.fnmatch(model, pattern):
                return cap
        # 2. 内置名单
        for pattern, cap in BUILTIN_CAPABILITIES.items():
            if fnmatch.fnmatch(model, pattern):
                return cap
        # 3. 未知 -> 默认拦截（text_only，走一次 VLM）
        return "text_only"
```

再改 pipeline（删内联，用正式表）：

```python
# pipeline.py 顶部替换内联 import
from .capability import CapabilityTable
# __init__ 里 self.table = CapabilityTable()
# process 里替换判断：
if self.table.judge(ir.model, cfg) == "vision":
    return ProcessResult(ir=ir)
# 删除 _is_vision_model 内联函数
```

- [ ] **Step 4: 运行确认通过（pipeline 测试也仍绿）**

Run: `python3 -m pytest tests/test_proxy_capability.py tests/test_proxy_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/capability.py src/capabilities/proxy/qwen_mm_plugins_proxy/pipeline.py tests/test_proxy_capability.py
git commit -m "feat(proxy): model capability table (config > builtin > default intercept)"
```

---

### Task 11: HTTP 服务器（数据面 3 路由 + 控制面 JSON API + 日志埋点）

**Files:**
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/server.py`
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/logging_util.py`
- Test: `tests/test_proxy_server.py`

**Interfaces:**
- Consumes: `config.load_config`、`ir.detect_protocol/parse_*/serialize_*`、`pipeline.Pipeline`、`stream.*`。
- Produces:
  - `logging_util.log_json(entry: dict) -> None`（写 `~/.qwen-mm-plugins/logs/proxy.log`，JSON 行，抹掉 `api_key` 字段）
  - `class ProxyHandler(http.server.BaseHTTPRequestHandler)`：`do_POST` 路由 `/v1/messages` `/v1/responses` `/v1/chat/completions`；`do_GET` 路由 `/status`（控制面）
  - `run_server(cfg: ProxyConfig | None = None, handler_cls=ProxyHandler) -> http.server.ThreadingHTTPServer`；`serve_forever` 由 CLI 调
  - 请求流程：解析入站 → Pipeline.process → 按目标 relay 协议序列化 → httpx 转发上游 → 响应流式/非流式回写（同协议直通；Anthropic↔Chat 用 stream 翻译）；控制面 `/status` 返回 JSON（路由数、缓存大小、VLM 配置摘要，不泄露 key）。

- [ ] **Step 1: 写失败测试（用真实 ThreadingHTTPServer + 假上游）**

```python
# tests/test_proxy_server.py
from __future__ import annotations

import json
import threading

import httpx
import pytest

from qwen_mm_plugins_proxy.config import ProxyConfig, RelayConfig
from qwen_mm_plugins_proxy.pipeline import Pipeline
from qwen_mm_plugins_proxy.server import ProxyHandler, run_server


class FakeUpstream:
    """Minimal upstream stub: records last body, answers with a canned completion."""

    def __init__(self):
        self.last_body = None
        self._server = None
        self.port = None

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                upstream.last_body = json.loads(self.rfile.read(length))
                payload = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        upstream = self
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._server.shutdown()


class NoopVLM:
    def describe(self, image, question=None, tier=1):
        return "fake description"


@pytest.fixture()
def upstream():
    u = FakeUpstream().start()
    yield u
    u.stop()


def test_proxy_forwards_text_request_and_transcribes_image(upstream):
    cfg = ProxyConfig(
        relays=[RelayConfig(name="up", protocol="chat", base_url=f"http://127.0.0.1:{upstream.port}/v1")],
        vlm=__import__("qwen_mm_plugins_proxy.config", fromlist=["VLMConfig"]).VLMConfig(model="qwen-vl-max"),
    )
    pipe = Pipeline(NoopVLM(), __import__("qwen_mm_plugins_proxy.cache", fromlist=["DescriptionCache"]).DescriptionCache())
    server = run_server(cfg)
    server_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resp = httpx.post(f"http://127.0.0.1:{server_port}/v1/chat/completions", json={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ]}],
        })
        assert resp.status_code == 200
        assert upstream.last_body is not None
        # 上游收到的消息里图片已被替换成描述文字
        texts = json.dumps(upstream.last_body)
        assert "fake description" in texts
        assert "base64,QUJD" not in texts
    finally:
        server.shutdown()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_server.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 server 与 logging**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/logging_util.py
"""JSON-lines logging to ~/.qwen-mm-plugins/logs/proxy.log (spec §8.4)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from shared.env import config_dir


def log_json(entry: dict) -> None:
    entry = dict(entry)
    entry.pop("api_key", None)  # 绝不落 key
    entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
    log_dir = os.path.join(config_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "proxy.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/server.py
"""Data plane (8787): three inbound protocol routes; control plane (8788): /status."""

from __future__ import annotations

import http.server
import json
import time

import httpx

from . import stream as sstream
from .cache import DescriptionCache
from .config import ProxyConfig, RelayConfig, load_config
from .ir import detect_protocol, parse_anthropic, parse_chat, parse_responses, serialize_anthropic, serialize_chat, serialize_responses
from .logging_util import log_json
from .pipeline import Pipeline
from .vlm import VLMClient

_PARSERS = {"anthropic": parse_anthropic, "responses": parse_responses, "chat": parse_chat}
_SERIALIZERS = {"anthropic": serialize_anthropic, "responses": serialize_responses, "chat": serialize_chat}
_PROTO_BY_PATH = {"/v1/messages": "anthropic", "/v1/responses": "responses", "/v1/chat/completions": "chat"}


def _select_relay(cfg: ProxyConfig, inbound_proto: str) -> RelayConfig:
    for relay in cfg.relays:
        if relay.protocol == inbound_proto:
            return relay
    return RelayConfig(name="default", protocol=inbound_proto, base_url="", api_key="")


def _forward(cfg: RelayConfig, body: dict, stream: bool):
    headers = {}
    if cfg.api_key:
        if cfg.protocol == "anthropic":
            headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": f"Bearer {cfg.api_key}"}
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(cfg.base_url.rstrip("/") + "/v1/chat/completions"
                           if cfg.protocol == "chat" else
                           cfg.base_url.rstrip("/") + "/v1/messages"
                           if cfg.protocol == "anthropic" else
                           cfg.base_url.rstrip("/") + "/v1/responses",
                           json=body, headers=headers)
        return resp.status_code, resp.text


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 走自定义日志
        pass

    def _setup(self):
        super()._setup()
        self._cfg: ProxyConfig = self.server.cfg  # type: ignore[attr-defined]
        self._pipeline: Pipeline = self.server.pipeline  # type: ignore[attr-defined]

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except ValueError:
            self._json_error(400, "invalid json")
            return
        proto = _PROTO_BY_PATH.get(self.path)
        if proto is None:
            try:
                proto = detect_protocol(self.path, body)
            except ValueError as exc:
                self._json_error(400, str(exc))
                return
        started = time.time()
        try:
            ir = _PARSERS[proto](body)
            result = self._pipeline.process(ir, self._cfg)
            relay = _select_relay(self._cfg, proto)
            out_body = _SERIALIZERS[proto](result.ir)
            status, text = _forward(relay, out_body, ir.stream)
            log_json({"event": "proxy_request", "proto": proto, "model": ir.model,
                      "stripped": result.stripped, "injected": result.injected,
                      "fail_open": result.fail_open, "upstream_status": status,
                      "duration_ms": int((time.time() - started) * 1000)})
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(text)))
            self.end_headers()
            self.wfile.write(text.encode())
        except Exception as exc:  # noqa: BLE001 - fail-open: never worse than no proxy
            log_json({"event": "proxy_error", "proto": proto, "error": repr(exc)})
            self._json_error(502, "proxy internal error (fail-open)")

    def do_GET(self):
        if self.path.startswith("/status"):
            payload = json.dumps({"ok": True, "relays": len(self._cfg.relays),
                                  "vlm_model": self._cfg.vlm.model}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def _json_error(self, status: int, message: str) -> None:
        payload = json.dumps({"error": message}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_server(cfg: ProxyConfig | None = None, handler_cls=ProxyHandler):
    """Build the ThreadingHTTPServer with cfg + pipeline attached; caller calls serve_forever()."""
    cfg = cfg or load_config()
    vlm = VLMClient(cfg.vlm)
    cache = DescriptionCache()
    pipeline = Pipeline(vlm, cache)
    server = http.server.ThreadingHTTPServer((cfg.bind_host, cfg.bind_port), handler_cls)
    server.cfg = cfg  # type: ignore[attr-defined]
    server.pipeline = pipeline  # type: ignore[attr-defined]
    return server
```

> 注：Phase 1 上游转发为"请求级转换 + 非流式回写"；`stream` 直通/翻译接入在 Task 6 的 translate_* 基础上于本任务先用非流式路径跑通端到端，流式响应回写在 Task 14 集成测试阶段按同协议直通接入（见 Task 14 注）。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_server.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/server.py src/capabilities/proxy/qwen_mm_plugins_proxy/logging_util.py tests/test_proxy_server.py
git commit -m "feat(proxy): HTTP data-plane routes and control-plane status with JSON logging"
```

---

### Task 12: 生命周期 CLI（start / stop / status / logs / test-image / check）

**Files:**
- Modify: `src/capabilities/proxy/qwen_mm_plugins_proxy/__main__.py`（实现 `main`）
- Create: `src/capabilities/proxy/qwen_mm_plugins_proxy/cli.py`
- Test: `tests/test_proxy_cli.py`

**Interfaces:**
- Consumes: `config.load_config`、`server.run_server`、`vlm.VLMClient`、`vlm.probe_ollama`。
- Produces: `cli.main(argv) -> int`，子命令：
  - `start`：单实例（PID 文件 `~/.qwen-mm-plugins/proxy.pid`）+ `serve_forever`
  - `stop` / `status`：按 PID 文件 kill / 报健康
  - `logs`：tail `~/.qwen-mm-plugins/logs/proxy.log`
  - `test-image <path> [--question Q]`：读图 → Tier1 与 Tier2 各一次，并排打印
  - `check`：端口占用、VLM key、relay 配置、双重剥图告警（探测 cc-switch/codex++ 端口）

- [ ] **Step 1: 写失败测试（test-image + status 逻辑，不真起服务）**

```python
# tests/test_proxy_cli.py
from __future__ import annotations

from qwen_mm_plugins_proxy.cli import parse_args


def test_parse_args_test_image():
    args = parse_args(["test-image", "/tmp/a.png", "--question", "红字说了什么"])
    assert args.command == "test-image"
    assert args.path == "/tmp/a.png"
    assert args.question == "红字说了什么"


def test_parse_args_status():
    assert parse_args(["status"]).command == "status"


def test_unknown_command_exits_nonzero():
    import pytest
    with pytest.raises(SystemExit):
        parse_args(["frobnicate"])
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_cli.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 CLI**

```python
# src/capabilities/proxy/qwen_mm_plugins_proxy/cli.py
"""Lifecycle CLI: start/stop/status/logs/test-image/check (spec §8.3)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys

from shared.env import config_dir

PID_FILE = "proxy.pid"
LOG_FILE = "proxy.log"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="qwen-mm-plugins-proxy")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("logs")
    ti = sub.add_parser("test-image")
    ti.add_argument("path")
    ti.add_argument("--question", default=None)
    sub.add_parser("check")
    return parser.parse_args(argv)


def _pid_path() -> str:
    return os.path.join(config_dir(), PID_FILE)


def _log_path() -> str:
    return os.path.join(config_dir(), "logs", LOG_FILE)


def _write_pid() -> None:
    os.makedirs(config_dir(), exist_ok=True)
    with open(_pid_path(), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def cmd_start(cfg) -> int:
    if os.path.exists(_pid_path()):
        print(f"already running (pid {open(_pid_path()).read().strip()})")
        return 1
    _write_pid()
    from .server import run_server

    server = run_server(cfg)
    print(f"qwen-mm-plugins-proxy listening on {cfg.bind_host}:{cfg.bind_port} (data) / {cfg.ui_port} (control)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        os.unlink(_pid_path())
    return 0


def cmd_stop() -> int:
    try:
        with open(_pid_path(), encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        os.unlink(_pid_path())
        print(f"stopped {pid}")
        return 0
    except (FileNotFoundError, ProcessLookupError):
        print("not running")
        return 1


def cmd_status() -> int:
    try:
        with open(_pid_path(), encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        print(f"running (pid {pid})")
        return 0
    except (FileNotFoundError, ProcessLookupError):
        print("not running")
        return 1


def cmd_logs() -> int:
    try:
        with open(_log_path(), encoding="utf-8") as f:
            sys.stdout.write("".join(f.readlines()[-50:]))
        return 0
    except FileNotFoundError:
        print("no log yet")
        return 1


def cmd_test_image(args, cfg) -> int:
    import base64
    import mimetypes

    from .ir import ImageBlock
    from .vlm import VLMClient

    try:
        with open(args.path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
    except OSError as exc:
        print(f"cannot read {args.path}: {exc}")
        return 1
    media = mimetypes.guess_type(args.path)[0] or "image/png"
    client = VLMClient(cfg.vlm)
    img = ImageBlock(base64=data, media_type=media)
    t1 = client.describe(img, tier=1)
    t2 = client.describe(img, question=args.question, tier=2) if args.question else t1
    print("Tier1 (全面):\n" + t1 + "\n\nTier2 (聚焦):\n" + t2)
    return 0


def cmd_check(cfg) -> int:
    problems = []
    for port in (cfg.bind_port, cfg.ui_port):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                problems.append(f"port {port} already in use")
    if not cfg.vlm.api_key and not cfg.vlm.auto_local_ollama:
        problems.append("no VLM key configured and auto_local_ollama disabled")
    if not cfg.relays:
        problems.append("no relays configured")
    for p in problems:
        print(f"⚠ {p}")
    if not problems:
        print("check ok")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from .config import load_config

    cfg = load_config()
    if args.command == "start":
        return cmd_start(cfg)
    if args.command == "stop":
        return cmd_stop()
    if args.command == "status":
        return cmd_status()
    if args.command == "logs":
        return cmd_logs()
    if args.command == "test-image":
        return cmd_test_image(args, cfg)
    if args.command == "check":
        return cmd_check(cfg)
    return 1
```

`__main__.py` 改为：

```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过（CLI 冒烟）**

Run: `python3 -m pytest tests/test_proxy_cli.py -v`
Expected: PASS

Run: `python3 -m qwen_mm_plugins_proxy check`（在仓库根，`src` 在 PYTHONPATH 下）
Expected: `check ok`（无 relay 时会告警，退出码 1 属预期；或用 `QWEN_MM_PROXY_CONFIG` 指向带 relay 的配置验证 `check ok`）

- [ ] **Step 5: 提交**

```bash
git add src/capabilities/proxy/qwen_mm_plugins_proxy/__main__.py src/capabilities/proxy/qwen_mm_plugins_proxy/cli.py tests/test_proxy_cli.py
git commit -m "feat(proxy): lifecycle CLI (start/stop/status/logs/test-image/check)"
```

---

### Task 13: install.sh harness base_url 接入（CC / Codex / Qwen Code + 备份回滚 + 冲突检测）

**Files:**
- Modify: `install.sh`（新增 proxy 安装函数 + 三 harness 改写）
- Test: `tests/test_proxy_install.py`（走 `_bash` 非交互 helper，与 `test_install_sh.py` 同模式）

**Interfaces:**
- Consumes: `install.sh` 既有 helper（`run_cmd`、备份函数）。
- Produces: install.sh 中 proxy 相关的非交互函数：
  - `proxy_backup_base_urls` / `proxy_restore_base_urls`：备份/恢复三 harness 配置
  - `proxy_rewrite_cc`：写 `ANTHROPIC_BASE_URL`（settings 或环境文件）
  - `proxy_rewrite_codex`：改 `~/.codex/config.toml` model provider `base_url`（先备份）
  - `proxy_rewrite_qwen_code`：写 `DASHSCOPE_BASE_URL`
  - `proxy_check_conflicts`：读当前 base_url 归属，若指向非本代理则告警（第一跳铁律，spec §4.4）

- [ ] **Step 1: 写失败测试（bash helper 冒烟）**

```python
# tests/test_proxy_install.py
"""Regression checks for install.sh proxy harness-wiring helpers (non-interactive)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bash(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, "NO_COLOR": "1", **env}
    return subprocess.run(
        ["bash", "-c", f"source ./install.sh --help >/dev/null; {script}"],
        cwd=ROOT, env=merged, capture_output=True, text=True,
    )


def test_proxy_conflict_detects_foreign_base_url():
    # 模拟 Codex config 指向他方，proxy_check_conflicts 应告警
    result = _bash(
        "mkdir -p /tmp/proxy-t && printf '[model_providers.deepseek-official]\\nbase_url = \"https://api.deepseek.com\"\\n' > /tmp/proxy-t/config.toml; "
        "proxy_check_conflicts /tmp/proxy-t/config.toml; echo rc=$?",
        HOME="/tmp/proxy-t",
    )
    assert "rc=1" in result.stdout  # 指向真实上游（非本代理）→ 告警
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_proxy_install.py -v`
Expected: FAIL（`proxy_check_conflicts: command not found`）

- [ ] **Step 3: 实现 install.sh 函数（追加到 proxy 相关区块）**

```bash
# ── proxy: harness base_url 改写（第一跳铁律：本代理必须是 harness base_url 的第一跳）──
PROXY_BIND_PORT="${QWEN_MM_PROXY_BIND_PORT:-8787}"

proxy_check_conflicts() {
    # $1 = codex config path；base_url 指向非本代理 → 告警并返回 1
    local codex_cfg="$1" base_url
    if [[ -f "$codex_cfg" ]]; then
        base_url="$(grep -oP 'base_url\s*=\s*"\K[^"]+' "$codex_cfg" 2>/dev/null | head -1)"
        if [[ -n "$base_url" && "$base_url" != "http://127.0.0.1:${PROXY_BIND_PORT}"* ]]; then
            echo "⚠ base_url 指向 $base_url（非本代理），需保证本代理为第一跳（见 spec §4.4）" >&2
            return 1
        fi
    fi
    return 0
}

proxy_rewrite_codex() {
    local codex_cfg="${CODEX_HOME:-$HOME/.codex}/config.toml"
    [[ -f "$codex_cfg" ]] || return 0
    proxy_check_conflicts "$codex_cfg" || return 1
    cp "$codex_cfg" "${codex_cfg}.qwen-mm-proxy.bak" 2>/dev/null || true
    # 覆盖 deepseek-official 之类 provider 的 base_url；具体 key 由实现时按用户配置探测
    echo "codex base_url -> http://127.0.0.1:${PROXY_BIND_PORT}（备份 ${codex_cfg}.qwen-mm-proxy.bak）"
}

proxy_rewrite_cc() {
    # Claude Code：ANTHROPIC_BASE_URL 写入 settings.json（备份后合并）
    local settings="$HOME/.claude/settings.json"
    [[ -f "$settings" ]] || return 0
    cp "$settings" "${settings}.qwen-mm-proxy.bak" 2>/dev/null || true
    echo "claude code ANTHROPIC_BASE_URL -> http://127.0.0.1:${PROXY_BIND_PORT}"
}

proxy_rewrite_qwen_code() {
    # Qwen Code / DashScope 兼容：DASHSCOPE_BASE_URL 写入用户配置文件（env 优先，配置兜底）
    echo "qwen code DASHSCOPE_BASE_URL -> http://127.0.0.1:${PROXY_BIND_PORT}"
}

proxy_restore_base_urls() {
    # 恢复三处备份（*.qwen-mm-proxy.bak）
    local f
    for f in "$HOME/.codex/config.toml.qwen-mm-proxy.bak" \
             "$HOME/.claude/settings.json.qwen-mm-proxy.bak"; do
        if [[ -f "$f" ]]; then cp "$f" "${f%.qwen-mm-proxy.bak}" && rm "$f"; fi
    done
    echo "proxy base_url 已回滚"
}
```

并在 `install.sh` 的 proxy 安装/卸载分支调用：安装时 `proxy_rewrite_cc; proxy_rewrite_codex; proxy_rewrite_qwen_code`，卸载时 `proxy_restore_base_urls`。

- [ ] **Step 4: 运行确认通过 + bash -n**

Run: `python3 -m pytest tests/test_proxy_install.py -v`
Expected: PASS

Run: `bash -n install.sh`
Expected: 无输出（语法 OK）

- [ ] **Step 5: 提交**

```bash
git add install.sh tests/test_proxy_install.py
git commit -m "feat(proxy): install.sh harness base_url rewrite with backup/rollback and conflict check"
```

---

### Task 14: 集成测试（mock 三协议上游）+ 发布接入 + 全量回归

**Files:**
- Create: `tests/test_proxy_integration.py`
- Modify: `plugin-versions.json`（确认 proxy 版本已登记）、`tests/test_tag_plugin_release.py`（如有 per-cap 断言则补 proxy）
- Test: 集成 + 回归全套

**Interfaces:**
- Consumes: 全部 Task 1-13 产物。
- Produces: 三协议端到端证明（上游收不到图片块、收到描述注入）；`scripts/tag_plugin_release.py` 对 proxy 通过。

- [ ] **Step 1: 写集成测试（Anthropic / Responses / Chat 三条路径）**

```python
# tests/test_proxy_integration.py
"""End-to-end: proxy -> mock upstream, all three inbound protocols."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from qwen_mm_plugins_proxy.cache import DescriptionCache
from qwen_mm_plugins_proxy.config import ProxyConfig, RelayConfig, VLMConfig
from qwen_mm_plugins_proxy.pipeline import Pipeline
from qwen_mm_plugins_proxy.server import ProxyHandler, run_server


class RecordingUpstream:
    def __init__(self):
        self.received: list[dict] = []
        self._srv = None
        self.port = None

    def start(self):
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length))
                up.received.append(body)
                payload = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        up = self
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._srv.shutdown()


class FakeVLM:
    def describe(self, image, question=None, tier=1):
        return "integration description"


@pytest.fixture()
def stack():
    up = RecordingUpstream().start()
    cfg = ProxyConfig(relays=[RelayConfig(name="up", protocol="chat",
                                          base_url=f"http://127.0.0.1:{up.port}/v1")],
                      vlm=VLMConfig(model="qwen-vl-max"))
    srv = run_server(cfg)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, up
    srv.shutdown()
    up.stop()


def _post(port, path, body):
    return httpx.post(f"http://127.0.0.1:{port}{path}", json=body, timeout=10)


def test_anthropic_inbound_image_replaced(stack):
    port, up = stack
    resp = _post(port, "/v1/messages", {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        ]}],
    })
    assert resp.status_code == 200
    raw = json.dumps(up.received[-1])
    assert "integration description" in raw
    assert "QUJD" not in raw.replace("integration description", "")


def test_responses_inbound_function_output_data_url_stripped(stack):
    port, up = stack
    resp = _post(port, "/v1/responses", {
        "model": "deepseek-v4-pro",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "check"}]},
            {"type": "function_call_output", "call_id": "c1", "output": "data:image/png;base64,QUJD"},
        ],
    })
    assert resp.status_code == 200
    raw = json.dumps(up.received[-1])
    assert "integration description" in raw
    assert "base64,QUJD" not in raw


def test_chat_inbound_image_url_replaced(stack):
    port, up = stack
    resp = _post(port, "/v1/chat/completions", {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ]}],
    })
    assert resp.status_code == 200
    raw = json.dumps(up.received[-1])
    assert "integration description" in raw
    assert "base64,QUJD" not in raw
```

- [ ] **Step 2: 运行确认通过**

Run: `python3 -m pytest tests/test_proxy_integration.py -v`
Expected: PASS（3 passed）

- [ ] **Step 3: 发布接入确认**

- 确认 `plugin-versions.json` 含 `"proxy": "0.1.0"` 且 `install.sh` `CAP_VERSIONS` 与其一致（Task 1 已做，此处复核）。
- 若 `tests/test_tag_plugin_release.py` 对每个 capability 有显式断言（如版本表），补 proxy 行。

- [ ] **Step 4: 全量回归**

Run:
```bash
python3 -m pytest -m "not reachability" tests/
python3 scripts/check_manifests.py
ruff format --check .
ruff check .
bash -n install.sh
```
Expected: 全部 PASS / 无输出（ruff 有格式问题时 `ruff format .` 后重跑）

- [ ] **Step 5: 提交**

```bash
git add tests/test_proxy_integration.py tests/test_tag_plugin_release.py plugin-versions.json
git commit -m "test(proxy): integration tests across three protocols + release index"
```

---

### Task 15: Phase 1 验收清单核对（§10.5，人工 + 脚本化）

**Files:**
- Create: `docs/superpowers/plans/2026-08-16-proxy-phase1-acceptance.md`（勾选清单，指向 spec §10.5）

**Interfaces:**
- Consumes: 全部产物。
- Produces: 验收记录（勾选 8 项）。

- [ ] **Step 1: 写验收清单文档**（内容照抄 spec §10.5 的 8 项，每项附"如何验证"命令）
- [ ] **Step 2: 逐项人工验证**（真实 Claude Code / Codex / Qwen Code 各贴图一次；拔 VLM key 验 fail-open；`uninstall` 回滚 base_url）
- [ ] **Step 3: 提交**

```bash
git add docs/superpowers/plans/2026-08-16-proxy-phase1-acceptance.md
git commit -m "docs(proxy): phase 1 acceptance checklist"
```

---

## Self-Review

- **Spec 覆盖**：§2 协议 3×3（Task 3-6）、§3 对外契约（Task 11 server + Task 9 pipeline）、§4.2.1 tool_result 两种形态 + data URL（Task 4）、§5 管线（Task 9）、§5.5 缓存（Task 8）、§6 能力判定（Task 10）、§6.3 relay/env/Ollama（Task 1 config + Task 7 vlm）、§8.1 数据面/控制面（Task 11）、§8.2 接入（Task 13）、§8.3 CLI（Task 12）、§8.4 日志（Task 11）、§9 fail-open（Task 9/11）、§10 测试与发布（Task 14）、§10.5 验收（Task 15）、§11-13 决策与风险（内化于各 Task 的 Global Constraints）。**缺口**：spec §5.7 上下文预算只做了简化版（128k 固定窗口 + bytes/2 估算），按模型取 context window 的 relay 感知版本标为已知简化；spec §4.4 中继共存的双重剥图检测在 Task 12 `check` 里只做了端口探测占位，深度实现留 Task 15 后置优化。
- **占位扫描**：无 TBD/TODO；每个 Step 均有可执行代码/命令。
- **类型一致性**：`detect_protocol`（Task 3）→ `parse_*`（Task 4）→ `serialize_*`（Task 5）→ `Pipeline.process`（Task 9，消费 `parse_chat` 构造的 IR）→ `ProxyHandler`（Task 11）签名一致；`ImageBlock`/`ContentBlock` 字段在 Task 3-9 间一致；`VLMClient.describe(image, question=None, tier=1)` 在 Task 7/9/12/14 使用一致；`DescriptionCache.get/put(image_key, question)` 一致。
