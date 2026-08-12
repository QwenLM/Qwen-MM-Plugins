# 测试

[English](../en/testing.md) · **中文**

测试按行为分层，不维护容易过期的测试文件清单：

- **单元与 handler 测试**覆盖 schema、工具发现、数据转换和错误路径。
- **协议测试**通过真实 MCP client 执行 initialize → `tools/list` → `tools/call`。
- **仓库一致性检查**保护重复 manifest、发布 ref、包版本和有意复制的源码。
- **连通性测试**调用外部服务，默认不运行。

`tests/conftest.py` 会把 `src/` 和包含 server 的能力目录加入 `sys.path`，因此新增 MCP server
无需先构建 wheel。纯 Skill 能力测试 Skill 与 manifest 约定，不测试不存在的 server。

## 命令

运行 CI 使用的离线检查：

```bash
python3 -m pytest -m "not reachability" tests/
python3 scripts/check_manifests.py
ruff format --check .
ruff check .
```

只有在明确需要真实请求并已配置凭证时，才运行服务连通性测试：

```bash
QWEN_MM_RUN_REACHABILITY=1 python3 -m pytest -m reachability tests/
```

修改 shell 脚本时还要运行 `bash -n <script>`。开发过程中先运行相关测试模块或使用
`-k <pattern>`，完成后再运行完整离线测试。

## 测试范围

根据能力实际包含的组件编写测试：

1. 每个 MCP server 都需要 schema/发现以及 handler 成功和错误路径测试。
2. server 存在特殊启动、streaming 或 transport 行为时，增加协议测试。
3. reader 或 renderer 需要代表性输入时，加入小型确定性 fixture。大型可选样本放在
   `tests/assets/real/`，缺少依赖时应清晰跳过。
4. 两份文件或 manifest 字段必须同步时，增加 anti-drift assertion。
5. 纯 Skill 修改应验证 frontmatter、引用资源和 manifest 打包。

默认离线测试不得依赖凭证、GUI 应用、GPU 硬件或公网连通性。
