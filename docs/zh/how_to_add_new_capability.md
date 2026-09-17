# 添加新插件

[English](../en/how_to_add_new_capability.md) · **中文**

新增插件涉及两个仓库：

1. 在 **Qwen-MM-Plugins** 中实现并注册 `src/capabilities/<cap>/`，包括 Skill、说明、manifest、测试，以及需要时的 MCP server。
2. 在 **[QwenLM/qwen-mm-plugins-hub](https://github.com/QwenLM/qwen-mm-plugins-hub)** 中添加 cookbook 和公开 case 文件。插件页面、工具定义、Skill 预览和 token 估计由 Hub 自动生成，无需再维护一份工具目录。

Hub 读取本仓库的 `main` 分支，Hub 自身的内容在其 `main` 分支维护。两边的同步顺序见 [发布与刷新](hub.md#发布与刷新)。

每个发布插件都包含 `skill/SKILL.md`，MCP server 可选。有 server 时从 [`example`](../../src/capabilities/example/) 开始；纯 Skill 插件参考 [`edu-agent`](../../src/capabilities/edu-agent/) 的打包结构。只保留自己的插件需要的模板文件；`example` 本身不发布。

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

所有工具使用[同一套 docstring 约定](hub.md)：`TOOL` 只声明名称和 Pydantic 参数模型，
`handle` 的 Google-style docstring 写工具说明，并在 `Args:` 中逐一说明参数。

在 `tools/`（或build_registry定义的子包列表下）下新建 `.py`，导出两样东西即可:

```python
from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    message: str
    repeat: int = Field(default=1, ge=1, le=10)


TOOL = {"name": "echo", "args": EchoArgs}


def handle(arguments: dict) -> list[dict]:
    """Echo a message.

    Args:
        message: Text to echo back.
        repeat: Repeat count, from 1 to 10.
    """
    return [{"type": "text", "text": arguments["message"] * arguments.get("repeat", 1)}]
```

- `args` 是一个 Pydantic 模型，自动生成工具的 `inputSchema` 并校验每次调用;`handle` 收到普通 dict，返回 MCP content blocks（`text` / `image`）。
- 惰性 import:像 `swatch.py` 那样,把 `PIL` 放到 `handle` 里再 import，不影响其他工具。系统工具(ffmpeg 等)在 `__init__.py` 声明一张 `SYSTEM_DEPS` 表(每条只需 `label` + `tools` + `hint`;可选的 `extra`/`probe`/`startup` 见 `mcp_framework` 里的 SYSTEM_DEPS 引擎),框架据此统一渲染 `--check-system` 并在启动时告警;空表 = “No system tools required.”。

## 跑起来 / 装上

在虚拟环境中装好 Python 依赖后，直接测试 server：

```bash
python3 src/capabilities/example/qwen_mm_plugins_example --version
python3 src/capabilities/example/qwen_mm_plugins_example --check-system
```

把示例路径换成自己的能力路径。测试完整的 Skill 与 MCP 安装时，先完成下面的注册，再在独立 clone 中运行 `bash install.sh local`；提交前用 `bash install.sh local --restore` 恢复 tracked manifest。详见[本地开发](local_development.md)。普通 marketplace 安装解析的是 release tag，不是未提交的改动，也不是 Hub 的预览分支。

## 要改的地方

统一能力 ID：目录 `<yourname>`，插件及 Skill 名 `qwen-mm-plugins-<yourname>`，extra `<yourname>`，Python import `qwen_mm_plugins_<yourname_with_underscores>`。Skill 的 description 和 H1 应简明描述具体任务；正文写依赖和工作流程，支持文件放在 `skill/` 内。

有 server 时，把 `src/capabilities/example/` 复制成 `src/capabilities/<yourname>/` 并重命名 Python 包，完成第 1–4 步。纯 Skill 插件跳过这些 Python 打包步骤。

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
   把新 server 的 extra 加到 `all` profile；需要打包非 Python 文件时，补充 `[tool.setuptools.package-data]`。
5. 在 `plugin-versions.json` 中加入初始版本，并在规范的 `.claude-plugin/marketplace.json`
   （CodeBuddy 与 WorkBuddy 也读取该文件）中加入固定到对应 tag 的 `git-subdir` entry。从同类能力复制 `.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、`.qoder-plugin/plugin.json`，替换所有名称、版本和说明，并保持 marketplace description 与 `install.sh` 的 `CAP_DESC` 一致。有 server 时还需 `.mcp.json`：使用唯一的 `qwen-mm-plugins-<yourname>` server key，以及对应的 extra、入口和版本 tag；server 的 `__version__` 也须与 manifest 一致。
6. 在 `install.sh` 的 `CAP_ITEMS`、`CAP_VERSIONS`、`CAP_DESC` 同一位置添加插件信息。纯 Skill 插件还需加入 `CAP_SKILL_ONLY`。
7. 按组件补充 handler/schema 或 Skill/manifest 测试，更新受影响的 discovery 和安装器预期，详见[测试](testing.md)。新增配置时，写入 `src/shared/env.py:CONFIG_FIELDS`，同步 `install.sh:CONFIG_SPEC`，并用 `scripts/gen_env_docs.py` 重新生成[配置参考](../en/configuration.md)。

`__main__.py` 从 `src/capabilities/example/` **原样复制**——它从目录名推断 import 名，没有任何 per-server 字面量。
纯 skill 能力不写 `mcpServers`、也没有 `.mcp.json`，但仍保留三套 harness manifest。

## 添加 Hub cookbook

在 [QwenLM/qwen-mm-plugins-hub](https://github.com/QwenLM/qwen-mm-plugins-hub) 创建 `content/cookbooks/<yourname>/usage.md`，演示文件放在 `public/cases/<yourname>/<case>/assert/`；可选的交互页面入口是 `<case>/index.html`。[Hub 维护指南](hub.md#cookbook-与-case) 提供 Markdown 模板、contributor 配置和媒体链接约定。缺少 cookbook 时构建会失败。

在本仓库中英文 README 各添加简短入口，cookbook 链接使用 `https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/<yourname>/cookbook/`。不要把 cookbook 正文或 case 媒体再复制回本仓库。通用英文文档仍在 `docs/en/` 维护，由下一次 Hub 构建自动导入。

## 检查与发布

运行相关[离线检查](testing.md)，再按 [Hub 本地验证](hub.md#本地验证) 联合检查两个仓库。Hub 读取真实 registry；构建内容时不会执行 handler 或调用模型服务。

按[发布与刷新](hub.md#发布与刷新) 更新网站。Hub 预览上线不代表新插件已能通过固定 release tag 的 marketplace 安装；提供正式安装前仍需完成独立的[插件发布流程](releasing.md)。

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
