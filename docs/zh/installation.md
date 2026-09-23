# 安装

[English](../en/installation.md) · **中文**

## 选择代码来源

| 目标 | 命令 | 来源 |
|---|---|---|
| 安装正式版本 | `bash install.sh install` | 各能力最新的已发布 tag |
| 更新已有安装 | `bash install.sh update` | 当前发布目录 |
| 测试未发布代码 | `bash install.sh local` | 当前 checkout，包括未提交修改 |
| 回退单个能力 | 见[回退](#回退) | 指定的不可变 tag |

正式安装不会跟随 `main`。测试分支时，先 checkout 该分支，再使用 `local`。

## 引导式安装器

安装器支持 Claude Code、CodeBuddy、Codex、Qoder、OpenClaw、Qwen Code 和 Gemini CLI。
它调用各 harness 的原生安装机制，并将共享配置保存在
`~/.qwen-mm-plugins/config`。

这里的 `Qoder` 和 `Qwen Code` 不包括独立桌面应用 QoderWork 与 QwenWork；后两者请使用
[应用内安装](manual_harnesses.md)。

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash
```

菜单提供 **Install**、**Update**、**Configure**、**Verify** 和 **Uninstall**。每个能力独立安装，
由一个 Skill 和可选的 MCP server 组成。

### 更新

使用最新脚本，确保其中包含当前发布目录：

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash -s -- update
```

对于安装器可管理的安装，所选能力的 Skill 与 MCP 配置会一起更新。之后，安装器会通过
`--check-system` 启动目标 tag 的 MCP 包。已经打开的 harness 可能仍需重新加载：

| Harness | 让更新生效 |
|---|---|
| Claude Code | 执行 `/reload-plugins`，或重启 |
| CodeBuddy | 执行 `/reload-plugins`，或重启 |
| Codex | 新建 task，或重启 |
| Qoder | 执行 `/plugins reload`，或重启 |
| OpenClaw | 托管 Gateway 通常自动重启；否则执行 `openclaw gateway restart` |
| Qwen Code | 重启 |
| Gemini CLI | 执行 `/skills reload` 和 `/mcp reload`，或重启 |

### 回退

对于支持远程 tag 的引导式 harness，只选择 tag 对应的能力：

```bash
QMP_REF=qwen-mm-plugins-search-v1.0.1 bash install.sh install
```

## 非交互安装与配置

在脚本或 CI 中，为 `install` 或 `local` 同时指定 `--plugin` 和 `--harness`：

```bash
bash install.sh local --plugin core --harness codex
bash install.sh install --plugin core,search --harness claude
bash install.sh local --plugin qwen-mm-plugins-core --plugin search --harness qwen-code --dry-run
bash install.sh config-set DASHSCOPE_API_KEY="$DASHSCOPE_API_KEY" QWEN_MM_NATIVE_MODE=1
bash install.sh config-set 'QWEN_MM_CACHE=/path/with spaces/cache'
bash install.sh config-set QWEN_MM_CACHE=  # 清除覆盖值，恢复默认值
```

`--plugin` 支持能力简称、完整插件 ID、逗号分隔、重复传入，或用 `all` 选择全部。
`--harness` 选择一个目标：`claude`、`codebuddy`、`codex`、`qoder`、`openclaw`、`qwen-code`
或 `gemini`。运行 `bash install.sh --help` 可查看当前插件列表。

显式指定后会直接执行，无需终端，也不会出现安装器询问。请提前安装 harness CLI 和 `uv`/`uvx`
（纯 Skill 插件不需要 `uvx`）。缺少依赖、参数错误、原生命令失败或 MCP 启动检查失败时，
返回非零退出码。`--dry-run` 仅打印安装命令，不执行安装或改写 manifest，但可能查询 harness
当前的 marketplace。命令不带选项时仍使用原来的交互流程。

`config-set` 接受一项或多项 `KEY=VALUE`，字段来自[配置目录](../en/configuration.md#configure-catalog)。
写入前会校验所有参数，保留其他已有配置，且不输出配置值。包含空格的参数需加引号；值可以
包含 `=`，但必须为单行。空值表示删除该项。共享配置文件权限保持 `600`，可通过
`QWEN_MM_CONFIG` 或 `QWEN_MM_CONFIG_DIR` 指定位置；环境变量仍优先于文件中的值。
非交互安装会跳过配置菜单。

## 本地 checkout

使用路径稳定的专用 clone：

```bash
git clone https://github.com/QwenLM/Qwen-MM-Plugins.git
cd Qwen-MM-Plugins
git switch <development-branch>   # 可选
bash install.sh local
```

local 模式会把所选插件的 manifest 和 MCP 包来源指向当前 checkout，并加入 `uvx --refresh`。
开发期间，受 Git 管理的 manifest 会保留绝对本地路径。退出 local 模式时恢复正式来源：

```bash
bash install.sh local --restore
```

直接运行源码和定向调试方式见[本地开发](local_development.md)。

## 手动安装 Skill + MCP

DeepSeek Harness、Hermes Agent、opencode、pi、QwenPaw 或其他没有兼容 marketplace 的
harness 使用此方式。对于包含 MCP 的能力，以下三处名称必须一致：

- Skill：`src/capabilities/<cap>/skill`
- 包 extra：`qwen-mm-plugins[<cap>]`
- 入口：`qwen-mm-plugins-<cap>`

例如 `video-memory` 使用 `[video-memory]`，不是 `[memory]`。`edu-agent` 是纯 Skill。

```bash
# 将 Skill 目录复制或链接到 harness 的 Skill 目录，然后注册：
uvx --from \
  "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" \
  qwen-mm-plugins-<cap>
```

手动注册的 Skill 与 MCP 没有共享安装记录，因此 harness 通常无法发现或提醒两者版本不一致。
运行最新安装器，选择 **Update → other (manual / another harness)**，再把两处都更新到脚本打印的
同一 tag。使用软链接时，每个能力/tag 应使用独立 checkout，因为不同能力 tag 可能指向不同 commit。

其他 harness 的应用内步骤和具体配置示例见[其他 Harness 安装](manual_harnesses.md)。

## Windows（WSL2）

Windows 目前仅支持 Ubuntu WSL2。请在 WSL home 目录（例如 `~/code`）中 clone，不要使用
`/mnt/c` 等 Windows 挂载路径，然后在 WSL 中运行 Linux 安装命令。

```powershell
wsl --install -d Ubuntu
```

使用 Codex 时，选择 WSL2 agent 环境，并在同一环境中安装插件。原生 Windows 尚未验证。

## 依赖

`uvx` 会把各能力的 Python 依赖安装到隔离缓存中。其余输入主要是服务配置和系统程序。

### 常用服务配置

下表只列出大多数云端能力首次使用时需要的配置。**Configure** 中的所有设置见
[配置参考（英文）](../en/configuration.md)。

| 变量 | 用途 |
|---|---|
| `DASHSCOPE_API_KEY` | 云端媒体 API、纯文本模型的图片描述、内容生成和记忆构建（video-memory、omni-memory） |
| `SERPER_API_KEY` | Serper 网页搜索/抽取，以及所有反向图像搜索 |
| `TAVILY_API_KEY` | Tavily 网页搜索和页面抽取 |
| `EXA_API_KEY` | Exa 网页搜索和页面抽取 |
| `SERPLY_API_KEY` | Serply 网页搜索和页面抽取 |

本地 `core` 文件读取无需 API key。可通过安装器的 **Configure**、shell 环境变量或
`~/.qwen-mm-plugins/config` 设置；环境变量优先。

使用官方 DashScope 端点调用 Omni 工具时，超过 10 MB base64 限额的本地音视频会优先
上传到与当前模型、API key 绑定的百炼临时 OSS（单文件最大 1 GiB，目前约保留 48 小时），
随后以 `oss://` 地址请求模型；无需配置 `OSS_AK`、`OSS_SK`、`OSS_ENDPOINT` 或
`OSS_BUCKET`。非官方兼容网关如支持该流程，可通过 `DASHSCOPE_UPLOAD_POLICY_URL` 指定完整的
临时上传凭证接口。临时上传不可用或失败时，仍按原有规则转码、使用用户自管 OSS 或抽帧降级。

未设置 `QWEN_MM_SEARCH_BACKEND` 或设为 `auto` 时，文本搜索按固定顺序选择第一个已配置
key 的后端：Serper、Tavily、Exa、Serply。设为 `serper`、`tavily`、`exa` 或 `serply` 会固定使用该后端；
如果缺少对应 key，则直接报错，不会回退。
`image_search` 独立于 `QWEN_MM_SEARCH_BACKEND`，始终使用 Serper Lens；缺少
`SERPER_API_KEY` 时会直接报错。

### 常用系统工具

| 工具 | 用途 |
|---|---|
| `ffmpeg` | 音视频读取、memory、剪辑和渲染 |
| LibreOffice | Office 与 DrawIO 可视化 |
| TeX | LaTeX 可视化 |
| Chromium | 网页截图和 edu-agent 渲染 |
| `tesseract` | 从视频提取 Skill 时识别屏幕文字（可选） |
| Blender / FreeCAD | 对应的实时应用集成 |

运行 `bash install.sh verify` 或 `<entry> --check-system` 查看所选能力的具体要求。能力专属依赖
记录在对应 Skill 和 cookbook 中。

### 完整配置

服务端点、搜索路由、超时、缓存路径、video-memory 文件、OSS、应用主机和高级兼容性开关见
[配置参考（英文）](../en/configuration.md)。
