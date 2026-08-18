# 添加新能力/插件

[English](../en/how_to_add_new_capability.md) · **中文**

`src/` 下每个插件 = 一个短文件夹，可含 `skill/`（Agent Skill）和/或 `<import_name>/`（MCP server 包），两者都可选。**最快的方式:复制可运行的模板 [`src/capabilities/example/`](../../src/capabilities/example/) 改。**

> 写完插件后怎么配测试,见 [测试](testing.md)——conftest 会自动发现你的 server 包,但 schema baseline 等几步要手动加。

## 结构

```
src/capabilities/example/
├── skill/SKILL.md                  # Agent Skill（frontmatter: name/description + 正文）
└── qwen_mm_plugins_example/         # MCP server 包（目录名 == import 名，须是合法 Python 标识符）
    ├── __init__.py                 # __version__ + build_registry(__name__, ["tools"]) + SYSTEM_DEPS + list_tools
    ├── __main__.py                 # 通用入口 shim（从任一 server 原样复制）
    └── tools/                      # 每个工具一个 .py，导出 TOOL + handle，启动时自动发现(由 `build_registry` 指定目录)
        ├── echo.py                 # 返回纯文本
        ├── swatch.py               # 返回图片（纯色 PNG，用 shared.content.image）
        ├── film_strip.py           # 返回多帧图片（真实 video 工具返回帧的同一套路）
        ├── describe.py             # 调用 OpenAI 兼容 API（端点/key 经 shared.env；dry_run 可离线）
        └── config_probe.py         # 用 shared.env.get_env 读 env/config（env > config > default）
```

## 工具约定（自动发现）

在 `tools/`（或build_registry定义的子包列表下）下新建 `.py`，导出两样东西即可:

```python
from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    message: str = Field(description="Text to echo back.")
    repeat: int = Field(default=1, description="Repeat count (1-10).")


TOOL = {"name": "echo", "description": "...", "args": EchoArgs}


def handle(arguments: dict) -> list[dict]:
    ...
    return [{"type": "text", "text": ...}]  # 或 {"type": "image", "data": <base64>, "mimeType": ...}
```

- `args` 是一个 Pydantic 模型，自动生成工具的 `inputSchema` 并校验每次调用;`handle` 收到普通 dict，返回 MCP content blocks（`text` / `image`）。
- 惰性 import:像 `swatch.py` 那样,把 `PIL` 放到 `handle` 里再 import，不影响其他工具。系统工具(ffmpeg 等)在 `__init__.py` 声明一张 `SYSTEM_DEPS` 表(每条只需 `label` + `tools` + `hint`;可选的 `extra`/`probe`/`startup` 见 `mcp_framework` 里的 SYSTEM_DEPS 引擎),框架据此统一渲染 `--check-system` 并在启动时告警;空表 = “No system tools required.”。

## 跑起来 / 装上

```bash
# 从源码直接跑
python3 src/capabilities/example/qwen_mm_plugins_example --version
python3 src/capabilities/example/qwen_mm_plugins_example --check-system

# 装进某个 harness —— example 仅作模板提供(不在 marketplace 列出);
# 把它复制成你自己的能力、并加进 marketplace.json 后,安装你自己的:
claude plugin marketplace add <本地仓库路径或 git URL>
claude plugin install qwen-mm-plugins-<你的能力>@qwen-mm-plugins
```

## 要改的地方

把 `src/capabilities/example/` 复制成 `src/capabilities/<yourname>/`，把 `qwen_mm_plugins_example/` 改成你的 import 名，然后:

1. `pyproject.toml` `[project.scripts]` —— 加一个入口:
   ```toml
   qwen-mm-plugins-<yourname> = "<import_name>.__main__:main"
   ```
2. `pyproject.toml` `[project.optional-dependencies]` —— 加一个 extra 组（列你的 pip 依赖）:
   ```toml
   <yourname> = ["...your deps..."]
   ```
3. `pyproject.toml` `[tool.setuptools] package-dir` —— 把 import 名映射到目录:
   ```toml
   "<import_name>" = "src/capabilities/<yourname>/<import_name>"
   ```
4. `pyproject.toml` `[tool.setuptools.packages.find] where` —— 加上对应插件目录
   （`include = ["qwen_mm_plugins*"]` 已能匹配 `qwen_mm_plugins_*`;若用别的前缀，记得同时改 `include`）:
   ```toml
   where = [..., "src/capabilities/<yourname>"]
   ```
5. 在 `plugin-versions.json` 中加入初始版本，并在规范的 `.claude-plugin/marketplace.json`
   （CodeBuddy 与 WorkBuddy 也读取该文件）中加入固定到对应 tag 的 `git-subdir` entry。从现有
   能力复制三套 harness manifest；有 server 的能力还需 `.mcp.json`。运行
   `scripts/check_manifests.py`，再按[插件发布](releasing.md)打首个 tag。

`__main__.py` 从 `src/capabilities/example/` **原样复制**——它从目录名推断 import 名，没有任何 per-server 字面量。
纯 skill 能力不写 `mcpServers`、也没有 `.mcp.json`，但仍保留三套 harness manifest。

## 共享库复用代码

已经有一个共享库 `src/shared/`:

- `shared.env` —— 配置/常量 + `get_env`(读环境变量的唯一入口,call-time;优先级:环境变量 > `~/.qwen-mm-plugins/config` > default)（`TOKEN_SIZE`、`DEFAULT_*`、`IMAGE_BUDGET_TOKENS`/`VIDEO_BUDGET_TOKENS`、`MAX_RESPONSE_BYTES`…）
- `shared.content` —— 入参 guard + 错误块（`text_error` / `require_file` / `require_dep` / `default_output_path`）
- `shared.image` —— PIL 图像处理 + 分辨率计算（`draw_boxes`、`norm_to_pixel`、`save_image`、`budget_to_pixels`、`smart_resize`）
- `shared.video` —— 抽帧/视频信息 + 时间戳解析（`get_video_info`、`extract_frames_by_seeking`、`compute_dynamic_fps`、`parse_time`）
- `shared.cache` —— 派生产物缓存（`cache_dir`、`cached_path`）
- `shared.syscmd` —— 定位外部 CLI(含 PATH 恢复)（`which_tool`、`find_tool`）
- `shared.isolated_worker` —— 当原生库初始化、进程级全局状态或硬超时可能拖垮 MCP
  服务时，在干净解释器中运行 JSON 可序列化 callable（`run_isolated`）
- `shared.api_openai` —— OpenAI 兼容 chat 客户端（`call_openai_chat`、`resolve_openai_endpoint`）
- `shared.api_dashscope` —— DashScope 原生 REST 异步生成任务（`submit_dashscope_async`、`poll_dashscope_task`、`save_url_to_dir`、`retry_call`）

仅当调用可能死锁或导致解释器崩溃、持有非线程安全的进程级全局状态，或必须在硬超时后终止时，
才使用隔离 worker。普通阻塞 I/O 应继续走 handler 线程；已经隔离在外部 CLI 中的工作也不需要再套
一层 Python worker。worker 的参数和结果必须可 JSON 序列化。该隔离保护 MCP 进程及其 stdio
传输，但它不是安全沙箱，也不提供 CPU、内存或权限边界。
