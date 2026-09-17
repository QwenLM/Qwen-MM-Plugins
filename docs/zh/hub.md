# Hub 维护

[English](../en/hub.md) · **中文**

Hub 当前维护在 **[QwenLM/qwen-mm-plugins-hub](https://github.com/QwenLM/qwen-mm-plugins-hub)**，发布地址是 [Qwen MM Plugins Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/)。实现和注册能力从[添加新插件](how_to_add_new_capability.md)开始。

按内容归属维护：

| 内容 | 维护位置 |
|---|---|
| 插件简介 | Qwen-MM-Plugins 的能力 manifest、marketplace description 和安装器 `CAP_DESC`；Hub 读取 Codex manifest |
| Skill 指令及支持文件 | Qwen-MM-Plugins 的 `src/capabilities/<cap>/skill/` |
| 工具和参数说明 | Qwen-MM-Plugins 的 handler docstring；类型与校验仍在 Pydantic |
| 通用英文文档 | Qwen-MM-Plugins 的 `docs/en/`，每次 Hub 构建自动导入 |
| Cookbook、分类、tag、标题、contributor | Hub 的 `content/cookbooks/<cap>/usage.md` |
| 演示视频、图片、交互 case | Hub 的 `public/cases/<cap>/<case>/` |

Hub 从 `plugin-versions.json` 发现插件，读取真实 MCP registry 和 Skill，再计算 token。生成的 `data/*.json` 已被 Git 忽略，只存在于本地和构建产物中；Hub 不提交第二份源码或文档。不要手改生成内容、增加第二份插件表，或把 cookbook 复制回本仓库。

## 唯一工具约定

所有工具统一：`TOOL` 只包含 `name`、`args`，工具说明和参数说明写在 `handle` 的 Google-style docstring 中。

```python
from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    message: str
    repeat: int = Field(default=1, ge=1, le=10)


TOOL = {"name": "echo", "args": EchoArgs}


def handle(arguments: dict) -> list[dict]:
    """Echo text back to the caller.

    Args:
        message: Text to repeat.
        repeat: Number of repetitions, from 1 to 10.
    """
    return [{"type": "text", "text": arguments["message"] * arguments.get("repeat", 1)}]
```

开头写工具用途；`Args:` 为每个参数写一条说明，包括继承字段，不能遗漏或重复。嵌套对象的内部字段写在对应参数说明中。可选的 `Examples:` 会保留在公开工具说明里。无参数工具不需要 `Args:`。

Pydantic 只负责类型、默认值、别名、约束和 validator，说明只写在 docstring。不再提供显式 description 覆盖或格式开关。框架生成的同一份 schema 同时供 FastMCP 和 Hub 使用，不修改原始模型。

Skill 的 frontmatter `description` 说明何时使用、能完成什么；H1 使用具体任务名称，不重复仓库名。脚本、参考资料和素材保留在 `skill/` 下，Hub 展示 tracked 文件层级并链接源码；`SKILL.md` 默认预览前 50 行，可展开全文，不必为预览而删减源文件。

## Cookbook 与 case

在 Hub 创建 `content/cookbooks/<cap>/usage.md`。每个已注册插件都必须有该文件，YAML metadata 可选。以 `my-plugin` 为例：

```markdown
---
title: My Plugin
category: Understanding
tags: [image, video]
contributors: [QwenLM]
order: 10
---

# My Plugin

## Workflow

Describe the input, setup, steps, and expected result.

## Cases

[Demo](../../../public/cases/my-plugin/demo/assert/demo.mp4)

[Interactive case](../../../public/cases/my-plugin/demo/index.html)
```

Contributors 写 GitHub 账号名，不写 URL；头像和主页链接自动生成，默认 `QwenLM`。Tag 优先保留一两个有用的任务/模态标签。不填 metadata 时，标题从能力 ID 生成，分类为 `Other`，排序值为 `99`。

每个 case 独立存放所需文件：

```text
public/cases/my-plugin/demo/
├── index.html          # 可选的交互页面
└── assert/
    ├── demo.mp4
    ├── screenshot.png
    └── ...             # 此 case 使用的其他文件
```

沿用目录名 **`assert`**，不是 `assets`。像模板一样把视频或 HTML 链接单独放在一个段落，Hub 会替换为播放器或隔离的 iframe；正文中的行内链接仍是链接，图片使用普通 Markdown 图片语法。不要在内嵌预览旁重复放缩略图、“查看录屏”或下载提示。HTML 内用相对的 `assert/...` 路径引用文件，cookbook 路径会自动转换成网站地址，不需要另外托管公网媒体链接。

视频使用 H.264/YUV420P 的 MP4，有音轨时用 AAC，并启用 faststart。每个 case 文件应小于 25 MiB，这是 Hub 当前的构建限制。提交实际文件，不要提交符号链接或 Git LFS pointer。提交前检查录屏中的凭证、个人信息和素材分享权限。

## 本地验证

使用 Node 24、Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)：

```bash
git clone https://github.com/QwenLM/qwen-mm-plugins-hub.git
cd qwen-mm-plugins-hub
npm ci
SITE_BASE_PATH=/qwen-mm-plugins-hub npm run build
npm test
npm run dev
```

`dev` 和 `build` 都会先自动生成内容。首次运行会将配置的源码 clone 到已忽略的 `.sources/upstream`，由 `uv` 准备 Python 3.12 和导出依赖；后续复用该 checkout。使用 `npm run content:sync` 拉取配置分支和 tag 的最新状态，使用 `npm run content` 仅重新生成、不拉取。`npm test` 消费已生成内容并保持离线，新 clone 应先生成内容或构建。根域名构建（包括隔离的 PR 构建检查）不设置 `SITE_BASE_PATH`。

预览自己的插件修改时，先 commit 并保持 checkout 干净，再显式指定路径和分支；Hub 不会修改通过 `HUB_SOURCE_DIR` 指定的 checkout：

```bash
HUB_SOURCE_DIR=../Qwen-MM-Plugins HUB_SOURCE_REF=my-branch npm run dev
```

HEAD 必须对应指定分支。CI 通过 `HUB_SOURCE_COMMIT` 固定到实际检出的 SHA，因此也支持 detached PR head；源码链接始终绑定该 commit。

也可以使用已有 Python 环境代替 `uv`，安装依赖后设置 `HUB_PYTHON`。以下源码 checkout 应保持干净并对应配置分支：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '../Qwen-MM-Plugins[omni-memory]' -r scripts/requirements-export.txt
HUB_PYTHON="$PWD/.venv/bin/python" HUB_SOURCE_DIR=../Qwen-MM-Plugins npm run build
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
npm test
```

## 发布与刷新

1. 将插件侧修改合并到 Hub [`source.config.json`](https://github.com/QwenLM/qwen-mm-plugins-hub/blob/main/source.config.json) 指定的远程分支，当前为 `main`。新增插件时同时准备好 Hub cookbook，确保下一次构建能拿到两边内容。只有本地 commit 或未合并的 PR 不会更新公网 Hub。
2. 将 cookbook 和 case 文件 push 或合并到 Hub `main`，该 push 会触发 [Build and deploy plugin directory](https://github.com/QwenLM/qwen-mm-plugins-hub/actions/workflows/pages.yml)。仅上游发生变化时，Hub 每 30 分钟检查所选分支和能力 tag，无需跨仓库 secret；仅当这些输入或 Hub commit 与上次成功部署不同才构建。在 Hub `main` 上 **Run workflow** 可强制重建，但目录引用的 release tag 尚未全部发布时仍会等待。
3. 等构建和部署通过，再检查[公网 Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/) 的插件、cookbook 和 Docs 页面。构建统一刷新目录、cookbook、英文文档和 token 估计；失败不改变线上内容，下次定时检查会重新尝试尚未部署的变化。重试前先修复构建错误。

定时检查是兜底，不保证精确时效：GitHub 可能延迟运行，公开仓库 [60 天无活动后会停用定时工作流](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow?tool=cli)，届时需重新启用。

如需即时刷新，可在 **Qwen-MM-Plugins** 的仓库 Actions secrets 中配置 `HUB_DISPATCH_TOKEN`：使用只授权 **QwenLM/qwen-mm-plugins-hub**、具有 **Actions: write** 权限的 fine-grained token，并遵循组织审批要求。插件侧 `hub-refresh.yml` 在 `main` 或能力 tag push 后触发 Hub 的 `pages.yml`；未配置 secret 时正常跳过，仍由定时检查兜底。不要将 token 提交到任一仓库。普通 `GITHUB_TOKEN` 仅限当前仓库，不能承担这里的跨仓库访问。

英文指南继续维护在本仓库，不另建 Hub docs 正文。每份 `docs/en/**/*.md` 都要有 H1 标题和唯一的路由：文件名下划线转成连字符，嵌套目录也用连字符连接。英文指南间的相对链接在 Hub 内跳转。

## PR 构建检查

相关工作流和辅助脚本合并到两个仓库的默认分支后，插件 PR 会运行 **Hub documentation check**，使用精确 PR head 和 Hub `main` 构建并测试。此任务只有只读 token、没有 secrets，不调用模型服务，也不部署网站。新增插件需要先在 Hub `main` 准备好 cookbook，否则检查会失败。

直接在 PR 的 **Checks** 页签查看结果和日志，不使用评论机器人、预览包或额外预览托管。工作流摘要链接到[正式 Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/)，不是当前 PR 的预览；PR 修改需要合并且自动部署成功后才会显示。Fork PR 使用相同的只读检查；GitHub 可能要求维护者先批准构建。

## 分支与发布

页面显示所选源码分支，源码链接固定到对应 commit。发布 Hub 不会合并插件分支或发布 tag；默认安装器使用正式发布版本，可能与文档展示的开发快照不同。分支代码按[本地开发流程](local_development.md)测试，不要使用尚未发布的 release tag 安装。

Hub 的 `source.config.json` 保持指向插件 `main`。准备版本发布时，先按独立的[发布流程](releasing.md)发布所引用的能力 tag，再刷新 Hub 的发布链接；仅修改 cookbook 和 case 时，只需发布 Hub。
