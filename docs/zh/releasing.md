# 插件版本与发布

Qwen-MM-Plugins 只有一个 Python distribution，但插件独立发布。插件版本覆盖 skill、manifest、
MCP 配置、server 代码以及其 Git tag 能看到的共享代码。

## 版本模型

- [`plugin-versions.json`](../../plugin-versions.json) 是 distribution 与各插件最新版索引。
- 正式 tag 使用 `qwen-mm-plugins-<cap>-v<semver>`。
- Marketplace entry 与 `uvx --from` 固定到同一 tag；`main` 只用于开发。
- Wheel 包含全部 server package，但每个插件使用自己的 tag 和 `uvx` 环境，因此发布
  `search` 不会升级已安装的 `core`。
- `mcp_framework.__version__` 是整个 distribution / release train 的版本；各 server package
  的 `__version__` 才是插件版本。独立发布后它们不同是正常的。

每个能力独立遵循 SemVer：兼容修复升 patch；新增工具、backend 或可加性行为升 minor；工具
schema、删工具或配置不兼容时升 major。

## 准备发布

为代码或 skill 改动影响到的每个能力执行：

```bash
git fetch origin --tags --prune
python scripts/prepare_plugin_release.py search 1.1.0 --distribution-version 1.0.2
python scripts/check_manifests.py
python -m pytest tests/
```

脚本会同步索引、manifest、MCP ref、server 版本、marketplace 与安装器；不会 commit、tag 或
push。每个 tag 都构建同一 distribution，因此 distribution 版本单独递增；多个能力共用一个
release commit 时使用同一个 `--distribution-version`。

代码与发布元数据放在同一个 commit；CI 通过后在该 commit 上打 tag：

```bash
git tag -a qwen-mm-plugins-search-v1.1.0 -m "qwen-mm-plugins-search 1.1.0"
git push origin <release-branch>
git push origin qwen-mm-plugins-search-v1.1.0
```

已发布 tag 不移动；发布有误时增加 patch 版本。

## 每周 release train

准备好的常规改动大约每周批量发布；空周不发，关键修复随时发。多个 capability tag 可以指向
同一 commit；共享运行时代码变化时，所有受影响能力都要升版本。

`example` 只是开发模板，刻意不进入稳定版本索引与 marketplace。
