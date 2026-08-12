# Manual harness setup

**English** · [中文](../zh/manual_harnesses.md)

Use the [guided installer](installation.md#guided-installer) when it supports your harness. This
page is only for registering a Skill and MCP server separately.

Replace `<cap>` with `core`, `api`, `search`, `video-memory`, `video-edit`, `blender`, or `freecad`.
`edu-agent` is Skill-only. Use one immutable tag for both the Skill and MCP command:

```text
qwen-mm-plugins-<cap>-v<version>
```

## Claude Code (direct registration)

```bash
ln -s /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.claude/skills/qwen-mm-plugins-<cap>

claude mcp add qwen-mm-plugins-<cap> -- \
  uvx --from \
  "qwen-mm-plugins[<cap>] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-<cap>-v<version>" \
  qwen-mm-plugins-<cap>
```

For local source, replace the Git package spec with `/path/to/Qwen-MM-Plugins[<cap>]`.

## opencode

Copy the Skill to `~/.config/opencode/skills/qwen-mm-plugins-<cap>` and add:

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

Use `~/.config/opencode/opencode.json` or a project-level `opencode.json`.

## pi

pi supports Skills directly; MCP tools require the community adapter:

```bash
cp -r /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.pi/agent/skills/qwen-mm-plugins-<cap>
pi install npm:pi-mcp-adapter
```

Add the server to `~/.config/mcp/mcp.json`:

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

QwenPaw does not consume this repository's plugin manifests. Copy the Skill (symlinks are rejected),
then enable it:

```bash
cp -r /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.qwenpaw/workspaces/default/skills/qwen-mm-plugins-<cap>
qwenpaw skills list
qwenpaw skills config
```

Add the server under `mcp.clients` in `~/.qwenpaw/workspaces/default/agent.json`:

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

## Update a manual install

Run the current installer and choose **Update → other (manual / another harness)**. Replace both the
copied/linked Skill and MCP Git ref with the tag it prints, then reload the harness. The installer
cannot safely edit unknown harness paths or infer the version of a copied Skill.
