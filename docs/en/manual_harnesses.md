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

## DeepSeek Harness (developer preview)

Validated with `@deepseek-ai/dsh` 0.1.0-rc.6. DSH loads Skills from `$DSH_HOME/skills` (normally
`~/.dsh/skills`) and connects stdio MCP servers through its bundled `@deepseek-ai/dsh-mcp-client`;
it currently requires manual registration.

Install and start DSH once to create the `web` profile:

```bash
npm install --global @deepseek-ai/dsh@0.1.0-rc.6
dsh --profile web
```

DSH filters credential-like variables from MCP child environments, so write provider settings to
the shared config file first:

```bash
bash install.sh configure
```

Copy the Skill from the tag used by the MCP command:

```bash
dsh_home=${DSH_HOME:-"$HOME/.dsh"}
mkdir -p "$dsh_home/skills"
cp -R /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  "$dsh_home/skills/qwen-mm-plugins-<cap>"
```

Add the MCP row to `$DSH_HOME/profiles/web/cordis.patch.yml` (normally
`~/.dsh/profiles/web/cordis.patch.yml`). Replace an initial `[]`, or merge the row into the existing
array:

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

Add one child row per capability. Save the file, restart DSH, and open a new session:

```bash
dsh --profile web
```

**Compatibility:** DSH 0.1.0-rc.6 preserves MCP text and structured results but replaces image,
audio, and resource blocks with `content discarded`. Text results from `vision_chat`, OCR, ASR, and
search remain usable; workflows that depend on media returned by MCP are incomplete. See the
upstream
[`dsh-mcp-client` limitation](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/packages/mcp/mcp-client/README.md#known-limitations-and-deferred-work).

## Hermes Agent

Validated with Hermes Agent v0.19.0. The Hermes installation must include MCP support
(`hermes-agent[mcp]` or `hermes-agent[all]`). Copy the complete Skill directory from the same
immutable tag used by the MCP command. Do not use the URL-based Skill installer here: Hermes v0.19
may omit runtime files from referenced directories.

```bash
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
mkdir -p "$hermes_home/skills"
skill_target="$hermes_home/skills/qwen-mm-plugins-<cap>"
[ ! -e "$skill_target" ] || \
  mv "$skill_target" "$hermes_home/qwen-mm-plugins-<cap>.bak.$(date +%Y%m%d%H%M%S)"
cp -R /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  "$skill_target"
```

Register and verify the MCP server (`edu-agent` is Skill-only):

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

Confirm that the test reports `Connected` and a nonzero tool count; Hermes v0.19 may exit with
status 0 even after reporting a connection failure.

**Compatibility:** At the provider boundary, Hermes is similar to DSH, but the mechanism differs.
DSH discards MCP media blocks; Hermes caches returned images locally and sends only
`MEDIA:<local-path>` text to the provider. Text and structured results remain usable, but the
provider receives no image pixels. For `read_video`, timestamps and frame order remain while the
original multi-frame visual context is lost; rereading individual cached frames does not restore
that interleaved sequence.

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
