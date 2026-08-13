# 插件发布

[English](../en/releasing.md) · **中文**

Qwen-MM-Plugins 只发布一个 Python distribution，但每个能力独立管理版本。一次能力发布包含该
能力的 Skill、manifest、MCP 配置、server 代码，以及该 tag 可见的共享代码。

## 版本模型

| 版本 | 范围 | 唯一来源 |
|---|---|---|
| 插件版本 | 单个能力 | [`plugin-versions.json`](../../plugin-versions.json) → `plugins.<cap>` |
| Distribution 版本 | 仓库快照和共享 Python distribution | 同一文件中的 `distribution_version` |
| Marketplace metadata 版本 | 目录快照；不表示所有插件都发生变化 | Distribution 版本 |
| 插件 tag | 单个能力使用的不可变源码快照 | `qwen-mm-plugins-<cap>-v<semver>` |

Marketplace entry 与 MCP `uvx --from` 固定到同一个插件 tag；`main` 只用于开发。虽然每个 tag
都包含完整 distribution，但各插件启动独立的 tag 环境；发布 `search` 不会更新已安装的 `core`。

每个能力遵循 SemVer：兼容修复增加 patch，新增工具或兼容行为增加 minor，破坏 schema、删除工具
或不兼容配置增加 major。共享 runtime 变化需要发布所有受影响的能力。

## 发布清单

1. 在 PR 分支准备所有受影响的能力：

   ```bash
   git fetch origin --tags --prune
   python3 scripts/prepare_plugin_release.py search 1.1.0 --distribution-version 1.0.2
   python3 scripts/check_manifests.py
   python3 -m pytest -m "not reachability" tests/
   ```

   脚本会更新发布元数据和启动 ref，但不会 commit、tag 或 push。多个能力共用一个发布 commit
   时，应使用相同的 distribution version。

2. 将代码与生成的发布元数据放在同一 commit，创建 PR，并等待合并。

3. 在 `origin/main` 当前实际存在的 commit 上创建 annotated tag：

   ```bash
   python3 scripts/tag_plugin_release.py search
   git show qwen-mm-plugins-search-v1.1.0
   git push origin qwen-mm-plugins-search-v1.1.0
   ```

   该脚本会拉取 `origin/main` 和现有 tags，检查发布元数据与目标 tag 是否一致，并把自上一个
   capability tag 以来、实际修改该 capability 或其 cookbook 的非 merge commits 写入 tag
   message。shared runtime commits 会单独列出供人工判断；确认相关时使用
   `--include-shared <commit>` 纳入说明。使用 `--dry-run` 预览，或使用 `--push` 一次完成创建和
   推送。

   合并后再打 tag，可以避免 GitHub squash/rebase 导致 tag 脱离主线。脚本会拒绝覆盖本地或
   远端已有 tag。已发布 tag 不得移动；发现问题时发布新的 patch 版本。

4. 按[安装文档](installation.md)对公开 tag 做 smoke test。

## 发布周期

准备好的常规改动大约每周批量发布；空周不发，关键修复按需发布。多个能力 tag 可以指向同一个
合并 commit。`example` 是开发模板，不对外发布。
