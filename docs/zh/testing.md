# 测试

[English](../en/testing.md) · **中文**

`python3 -m pytest tests/`, 统一进行测试。

对于新插件，不需要显式安装， `conftest.py` 把 `src/` 和各 `src/capabilities/<cap>/` 加进 `sys.path` 并自动发现所有 server 包，`import qwen_mm_plugins_<yourname>` 即可, fixtures(`sample_image` / `sample_video`)也能复用。

## 现有测试

- `test_tools.py` —— 测试core server：in-process 调 `handle()`(工具发现 / schema / read_image / read_video / budget / 时间戳回归)+ 用 MCP SDK 的 stdio client 走完整 initialize → tools/list → tools/call。
- `test_mcp_framework.py` —— 共享框架：`tool_schema` / `build_registry` 的 schema 变换与工具发现。
- `test_api_clients.py` —— 共享网络层(`shared.api_openai` / `api_dashscope` / `retry`)。
- `test_video_memory.py` —— 测试video memory server：没建 graph_memory.json 时返回 isError。
- `test_build_merge.py` —— video-memory 构建：`build_graph.merge_chunks`。
- `test_video_edit.py` —— video-edit测试：只测 schema/handler。
- `test_example.py` —— example 能力的示例工具：只测 schema/handler。
- `test_blender.py` / `test_freecad.py` —— thin-client 能力(blender / freecad)：schema/handler 表面、无 app 监听时优雅降级、MCP stdio 桥。
- `test_launch_autoinstall.py` —— blender/freecad `--launch-app` 背后的免 root app 自动下载与失败诊断(hermetic;下载/解压环节 monkeypatch)。
- `test_renderers.py` / `test_visualize_real.py` —— `visualize()` 渲染测试。
- `test_repo_sync.py` —— video-memory build 副本一致性，以及逐插件 release 索引、manifest、
  marketplace tag、MCP ref 与安装器版本的一致性。

## 新插件要写哪几层

按插件「产出什么」逐层加,最少写第 1 层:

**1. Smoke test(必写)** `tests/test_<yourname>.py`。本地工具照 `test_example.py`,远程 / 需外部服务的照 `test_video_edit.py`(只测表面,不 live 调用):

```python
import qwen_mm_plugins_<yourname> as m

def test_lists_tools():
    assert {t["name"] for t in m.list_tools()} == {"tool_a", "tool_b"}

def test_handler():
    assert m.get_handler("tool_a")({"arg": "x"})[0]["type"] == "text"   # 或 image
```

**2. 渲染素材(工具产出图 / 文本时)** —— 小素材进 `tests/assets/` + 参数化(照 `test_renderers.py`),大素材进 `tests/assets/real/`(照 `test_visualize_real.py`);缺依赖 skip 不 fail。

**3. 协议层(server 有 stdio 行为时)** —— 用 conftest 的 `mcp_call()` 把 server 当子进程拉起走真实 MCP client(参考 `test_video_memory.py`)。

**4. 防漂移守卫** —— 在 `test_repo_sync.py` 加 byte-identical 断言(参考 video-memory 的 build 副本)。

## 跑

```bash
python3 -m pytest tests/                 # 全套(缺依赖自动 skip)
python3 -m pytest tests/test_<yourname>.py -v
ruff format . && ruff check . --fix      # 提交前
```

## 速查

- [ ] `tests/test_<yourname>.py` smoke —— 本地参考 `test_example.py`,远程参考 `test_video_edit.py`
- [ ] 有渲染 / 产出 → 素材进 `tests/assets/`(小)或 `real/`(大)+ 参数化
- [ ] server 有协议行为 → `mcp_call` 端到端,参考 `test_video_memory.py`
- [ ] 刻意重复文件 → `test_repo_sync.py` 加guard
- [ ] `pytest tests/` 全绿、`ruff check .` 干净
