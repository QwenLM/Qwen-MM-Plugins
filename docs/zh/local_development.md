# 本地开发 / 调试

[English](../en/local_development.md) · **中文**

四种本地调试方式,所有命令都在仓库根目录下运行。

## 1. 直接写 Python / 跑 pytest —— 可编辑安装

把qwen-mm-plugins以 editable 方式装进当前环境,之后 `import qwen_mm_plugins_core` 就能用,代码改动实时生效(不用重装)，这一步可以用来调试一些基础功能。

```bash
scripts/dev-install.sh          # 只装基础依赖(能 import、能从源码起 server)
scripts/dev-install.sh core     # vision + 全套 visualize
scripts/dev-install.sh all      # 全部(含 geopandas/trimesh/playwright 等重依赖)

python -c "import qwen_mm_plugins_core as p; print(len(p.SPECS), 'tools')"
python -m pytest tests/
```

装在当前激活的 venv 里(有 `uv` + venv 时用 uv,否则用 pip)。改依赖了才需要重跑。

## 2. 从源码直接起某个 server

```bash
python3 src/capabilities/core/qwen_mm_plugins_core --version
python3 src/capabilities/core/qwen_mm_plugins_core --check-system
# stdio测试:
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 src/capabilities/core/qwen_mm_plugins_core
```

`__main__.py` 会自动把 `src/` 和自己的能力目录加进 `sys.path`,所以任意 cwd 都能起。

## 3. 在 harness 里调 server 逻辑

安装方法二对应的注册逻辑：

```bash
# skill:软链本地 skill 目录
ln -s "$(pwd)/src/capabilities/core/skill" ~/.claude/skills/qwen-mm-plugins-core
# MCP:python3 跑这个目录 = 执行它的 __main__.py(即 server 入口,等价于 console 的 qwen-mm-plugins-core);改完重连即生效、无构建缓存
claude mcp add qwen-mm-plugins-core -- python3 "$(pwd)/src/capabilities/core/qwen_mm_plugins_core"
```

工具名是 `mcp__qwen-mm-plugins-core__<tool>`(手动装,没有 plugin 前缀)。直跑用当前 python 环境的依赖——所以先跑一遍 `scripts/dev-install.sh core`(或 `all`)把依赖备齐。改完代码让 harness 重连该 MCP(重开会话或 `/mcp` 重连)即加载新代码。

想更贴近生产(隔离环境 + 按 profile 装依赖),把命令换成 `uvx --from "$(pwd)[core]" qwen-mm-plugins-core`。本地源码一有改动 uvx 就会重建,所以让 harness 重连即可加载你的改动(只有在极少数它仍给出旧构建时,才需要加 `--refresh` 强制重建)。

## 4. 调整条插件链路(marketplace + install)用本地代码

要验证 plugin.json / marketplace.json / 安装注册本身对不对,但用本地未 push 的代码:把该能力的 plugin 清单临时指到本地 checkout,装完测,再还原。

```bash
scripts/dev-plugin.sh core          # 把 core 的 marketplace + MCP ref 从稳定 tag 切到当前 checkout
claude plugin marketplace add "$(pwd)"      # 市场指向本地目录 → 读工作区 manifest
claude plugin install qwen-mm-plugins-core@qwen-mm-plugins
# ... 测试(工具名带前缀:mcp__plugin_qwen-mm-plugins-core_qwen-mm-plugins-core__<tool>)...
scripts/dev-plugin.sh core --revert         # 还原清单, 提交之前执行
```

`dev-plugin.sh` 与 `bash install.sh local` 共用同一重写器：前者为单能力开发增加
`--refresh`，后者完成整套 harness 安装。手动测试后要还原，避免提交本地路径。
