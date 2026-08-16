# 本地开发

[English](../en/local_development.md) · **中文**

以下命令均从仓库根目录运行。快速源码调试使用虚拟环境；完整插件安装测试使用专用 clone。

## 快速源码循环

只安装当前任务需要的依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[core]'  # 所有能力使用 '.[all]'
```

直接从源码启动 server：

```bash
python3 src/capabilities/core/qwen_mm_plugins_core --version
python3 src/capabilities/core/qwen_mm_plugins_core --check-system
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 src/capabilities/core/qwen_mm_plugins_core
```

代码修改会在下次启动进程时生效。开发过程中运行定向测试，具体见[测试](testing.md)。

如果只需要连接一个实时 MCP，可在 harness 中直接注册源码入口，修改后重新连接。例如：

```bash
claude mcp add qwen-mm-plugins-core -- \
  python3 "$(pwd)/src/capabilities/core/qwen_mm_plugins_core"
# 清理：claude mcp remove qwen-mm-plugins-core
```

## 完整插件安装链路

需要验证 marketplace manifest、Skill 发现、MCP 注册以及 harness 完整安装流程时，运行：

```bash
bash install.sh local
```

安装器会把所选能力指向当前 checkout，并加入 `uvx --refresh`。该操作会在受 Git 管理的 manifest
中写入绝对本地路径，因此请使用专用 clone，并在安装期间保持路径不变。

提交代码或退出 local 模式前恢复正式来源：

```bash
bash install.sh local --restore
```
