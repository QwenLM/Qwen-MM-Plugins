# Local development

**English** · [中文](../zh/local_development.md)

Run commands from the repository root. Use a virtual environment for the fast source loop and a
dedicated clone for full plugin-install testing.

## Fast source loop

Install only the dependencies you need:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[core]'  # use '.[all]' for every capability
```

Run a server directly from source:

```bash
python3 src/capabilities/core/qwen_mm_plugins_core --version
python3 src/capabilities/core/qwen_mm_plugins_core --check-system
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 src/capabilities/core/qwen_mm_plugins_core
```

Code changes take effect on the next process start. Run targeted tests while iterating; see
[Testing](testing.md).

If you need only a live MCP connection, register the source entry in a harness and reconnect after
edits. For example:

```bash
claude mcp add qwen-mm-plugins-core -- \
  python3 "$(pwd)/src/capabilities/core/qwen_mm_plugins_core"
# cleanup: claude mcp remove qwen-mm-plugins-core
```

## Full plugin-install loop

Use this path to test marketplace manifests, Skill discovery, MCP registration, and the harness's
complete install flow:

```bash
bash install.sh local
```

The installer points the selected capabilities at the current checkout and adds `uvx --refresh`.
It intentionally writes absolute local paths into tracked manifests, so use a dedicated clone and
do not move it while installed.

Restore release sources before committing or leaving local mode:

```bash
bash install.sh local --restore
```
