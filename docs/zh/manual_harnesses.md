# 其他 Harness 安装

[English](../en/manual_harnesses.md) · **中文**

默认优先使用[引导式安装](installation.md#引导式安装器)。本页记录 WorkBuddy、QoderWork 与
QwenWork 的桌面应用 UI，以及直接注册 Skill + MCP 的备选方式。

## 桌面应用 UI

| 产品 | 安装入口 |
|---|---|
| CodeBuddy | [引导式安装器](installation.md#引导式安装器) |
| WorkBuddy | 下文的插件页面 |
| Qoder | [引导式安装器](installation.md#引导式安装器) |
| QoderWork | 下文的应用内任务 |
| Qwen Code | [引导式安装器](installation.md#引导式安装器) |
| QwenWork | 下文的应用内任务 |

### WorkBuddy（插件页面）

WorkBuddy 读取仓库的 `.claude-plugin/marketplace.json`，并把每个插件作为一个完整 bundle 安装。
打开「插件」，点击 `+`，添加 `https://github.com/QwenLM/Qwen-MM-Plugins.git`，再从该市场安装
所需的 `qwen-mm-plugins-*` 插件。更新或卸载也在同一页面完成。

### QoderWork 与 QwenWork（应用内任务）

在应用中新建任务，提供仓库 URL 和需要的能力名，让它安装完整的 Skill + MCP bundle。例如：

```text
从 https://github.com/QwenLM/Qwen-MM-Plugins 安装 qwen-mm-plugins-core。
Skill 与 MCP server 使用同一个已发布 tag，安装后确认 MCP 工具已上线。
```

完成后，在应用的 Skill 与 MCP/Connector 页面检查对应条目。更新时用当前正式版本重复该任务；
卸载时在这些页面同时移除两处条目。`edu-agent` 只有 Skill 条目。

## 直接注册 Skill + MCP

对于下文直接注册 Skill + MCP 的 harness，请将 `<cap>` 替换为 `core`、`api`、`search`、
`video-memory`、`omni-memory`、`video-edit`、`blender` 或 `freecad`。`edu-agent` 是纯 Skill。Skill 与 MCP
命令必须使用同一个不可变 tag：

```text
qwen-mm-plugins-<cap>-v<version>
```

### Claude Code

```bash
ln -s /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.claude/skills/qwen-mm-plugins-<cap>

claude mcp add qwen-mm-plugins-<cap> -- \
  uvx --from \
  "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" \
  qwen-mm-plugins-<cap>
```

使用本地源码时，将 Git 包规格替换为 `/path/to/Qwen-MM-Plugins[<cap>]`。

### opencode

将 Skill 复制到 `~/.config/opencode/skills/qwen-mm-plugins-<cap>`，然后添加：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "qwen-mm-plugins-<cap>": {
      "type": "local",
      "command": [
        "uvx", "--from",
        "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>",
        "qwen-mm-plugins-<cap>"
      ],
      "enabled": true
    }
  }
}
```

配置文件可以是 `~/.config/opencode/opencode.json` 或项目级 `opencode.json`。

### DeepSeek Harness（developer preview）

已使用 `@deepseek-ai/dsh` 0.1.0-rc.6 验证。DSH 从 `$DSH_HOME/skills`（通常为
`~/.dsh/skills`）加载 Skill，并通过内置 `@deepseek-ai/dsh-mcp-client` 连接 stdio MCP server；
目前没有自动注册命令，需要手动配置。引导式安装器的 **Configure** 和 **Verify** 不依赖
harness 专属注册，因此仍可使用。

安装并首次启动 DSH，让它创建 `web` profile：

```bash
npm install --global @deepseek-ai/dsh@0.1.0-rc.6
dsh --profile web
```

DSH 会过滤传给 MCP 子进程的凭据类环境变量，因此先把服务配置写入共享文件：

```bash
bash install.sh configure
```

从 MCP 命令对应的 tag checkout 复制 Skill：

```bash
dsh_home=${DSH_HOME:-"$HOME/.dsh"}
mkdir -p "$dsh_home/skills"
cp -R /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  "$dsh_home/skills/qwen-mm-plugins-<cap>"
```

将 MCP 行添加到 `$DSH_HOME/profiles/web/cordis.patch.yml`（通常为
`~/.dsh/profiles/web/cordis.patch.yml`）。如果文件内容为 `[]`，直接替换；否则合并到已有数组：

```yaml
- insert:
    - id: mcp-qwen-mm-plugins-<cap>
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: qwen-mm-plugins-<cap>
        transport: stdio
        command: uvx
        args:
          - '--from'
          - 'qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>'
          - 'qwen-mm-plugins-<cap>'
        cwd: !!js process.cwd()
```

每个 capability 添加一个子项。保存后重启 DSH，并新建会话：

```bash
dsh --profile web
```

**兼容性：** DSH 0.1.0-rc.6 可传递 MCP 文本和结构化结果，但会把 image、audio 和 resource
block 替换为 `content discarded`。`vision_chat`、OCR、ASR 和搜索等文本结果可用；依赖 MCP
返回媒体内容的流程尚不完整。参见上游
[`dsh-mcp-client` 限制](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/packages/mcp/mcp-client/README.md#known-limitations-and-deferred-work)。

### Hermes Agent

已使用 Hermes Agent v0.19.0 验证。Hermes 必须安装 MCP 支持（`hermes-agent[mcp]` 或
`hermes-agent[all]`）。请从 MCP 命令所用的同一不可变 tag 复制完整 Skill 目录。这里不要使用
基于 URL 的 Skill installer：Hermes v0.19 可能漏掉引用目录中的运行时文件。

```bash
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
mkdir -p "$hermes_home/skills"
skill_target="$hermes_home/skills/qwen-mm-plugins-<cap>"
[ ! -e "$skill_target" ] || \
  mv "$skill_target" "$hermes_home/qwen-mm-plugins-<cap>.bak.$(date +%Y%m%d%H%M%S)"
cp -R /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  "$skill_target"
```

注册并验证 MCP server（`edu-agent` 是纯 Skill）：

```bash
uvx_bin=$(command -v uvx)
hermes mcp add qwen-mm-plugins-<cap> \
  --command "$uvx_bin" \
  --connect-timeout 180 \
  --args --from \
  "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" \
  qwen-mm-plugins-<cap>
hermes mcp test qwen-mm-plugins-<cap>
```

请确认测试输出中包含 `Connected` 且工具数大于 0；Hermes v0.19 报告连接失败后仍可能
以状态码 0 退出。

**兼容性：** 在 provider 输入边界上，Hermes 与 DSH 类似，但机制不同。DSH 会丢弃 MCP 媒体
block；Hermes 会在本地缓存返回的图片，仅向 provider 发送 `MEDIA:<本地路径>` 文本。文本和
结构化结果仍可正常使用，但 provider 收不到图片像素。对于 `read_video`，时间戳和帧顺序会保留，
但原始多帧视觉上下文会丢失；逐帧重新读取缓存图片也无法恢复原来的交错序列。

### pi

pi 原生支持 Skill；MCP 工具需要社区 adapter：

```bash
cp -r /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.pi/agent/skills/qwen-mm-plugins-<cap>
pi install npm:pi-mcp-adapter
```

在 `~/.config/mcp/mcp.json` 中添加 server：

```json
{
  "settings": { "toolPrefix": "none" },
  "mcpServers": {
    "qwen-mm-plugins-<cap>": {
      "command": "uvx",
      "args": [
        "--from",
        "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>",
        "qwen-mm-plugins-<cap>"
      ]
    }
  }
}
```

### QwenPaw 2.0

QwenPaw 不读取本仓库的 plugin manifest。请复制 Skill（不支持软链接），然后启用：

```bash
cp -r /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.qwenpaw/workspaces/default/skills/qwen-mm-plugins-<cap>
qwenpaw skills list
qwenpaw skills config
```

在 `~/.qwenpaw/workspaces/default/agent.json` 的 `mcp.clients` 中添加 server：

```json
{
  "mcp": {
    "clients": {
      "qwen-mm-plugins-<cap>": {
        "name": "qwen-mm-plugins-<cap>",
        "enabled": true,
        "transport": "stdio",
        "command": "uvx",
        "args": [
          "--from",
          "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>",
          "qwen-mm-plugins-<cap>"
        ]
      }
    }
  }
}
```

### 更新

运行最新安装器并选择 **Update → other (manual / another harness)**。将已复制/链接的 Skill 与
MCP Git ref 同时替换为脚本打印的 tag，然后重新加载 harness。安装器不会修改未知 harness 的
路径，也无法可靠推断已复制 Skill 的版本。
