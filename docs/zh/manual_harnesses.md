# 手动配置其他 Harness

[English](../en/manual_harnesses.md) · **中文**

如果引导式安装器支持目标 harness，请优先使用[引导式安装](installation.md#引导式安装器)。本页
只说明如何分别注册 Skill 与 MCP server。

将 `<cap>` 替换为 `core`、`api`、`search`、`video-memory`、`video-edit`、`blender` 或
`freecad`。`edu-agent` 是纯 Skill。Skill 与 MCP 命令必须使用同一个不可变 tag：

```text
qwen-mm-plugins-<cap>-v<version>
```

## Claude Code（直接注册）

```bash
ln -s /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.claude/skills/qwen-mm-plugins-<cap>

claude mcp add qwen-mm-plugins-<cap> -- \
  uvx --from \
  "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" \
  qwen-mm-plugins-<cap>
```

使用本地源码时，将 Git 包规格替换为 `/path/to/Qwen-MM-Plugins[<cap>]`。

## opencode

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

## DeepSeek Harness（developer preview）

已使用 `@deepseek-ai/dsh` 0.1.0-rc.6 验证。DSH 从 `$DSH_HOME/skills`（通常为
`~/.dsh/skills`）加载 Skill，并通过内置 `@deepseek-ai/dsh-mcp-client` 连接 stdio MCP server；
目前没有自动注册命令，需要手动配置。

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

## pi

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

## QwenPaw 2.0

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

## 更新手动安装

运行最新安装器并选择 **Update → other (manual / another harness)**。将已复制/链接的 Skill 与
MCP Git ref 同时替换为脚本打印的 tag，然后重新加载 harness。安装器不会修改未知 harness 的
路径，也无法可靠推断已复制 Skill 的版本。
