## 重要：记忆子系统重构为 Hermes 风格

### 改动范围

内存模块全部按 Hermes-agent 的设计模式重写：

| 文件 | 操作 | 说明 |
|------|------|------|
| `memory/store.py` | 重写 | 移除 working/embedding/vector/retrieval，纯文件快照 + entry 级增删改 |
| `tool/builtins/memory.py` | 重写 | 合并为单一 `memory` 工具（add/replace/remove），移除 `memory_read`/`memory_write` |
| `agent/prompt.py` | 修改 | 移除 working 参数和 Layer 6（当前会话笔记） |
| `agent/loop.py` | 修改 | system prompt 从 store.get_memory/get_user/get_episodic 读取 |
| `agent/cli.py` | 修改 | `_build_memory()` 简化为纯 `MemoryStore(memory_dir=...)` |
| `memory/retrieval.py` | 删除 | FTS5 检索 |
| `memory/embedding.py` | 删除 | OpenAI embedding |
| `memory/vector.py` | 删除 | 向量存储 |
| `tool/toolsets.py` | 修改 | memory 工具集改为 `{"memory"}`，键名改为 `memories` 避免递归冲突 |
| `memory/__init__.py` | 修改 | 更新模块描述 |

### 关键设计

- **System prompt**：每轮 `run_conversation()` 构建时从 store 读取当前 MEMORY.md/USER.md/EPISODIC.md 完整内容注入
- **写入**：单 `memory` 工具，每次 add/replace/remove 返回完整条目列表 + 使用量统计（Hermes `_success_response` 模式）
- **无需 read 工具**：system prompt 已有内容，写入工具响应带回全量状态
- **条目分隔符**：`¶` `${ "" }`（Hermes 风格），支持独立增删改
- **注入检测**：复制 Hermes 的不可见字符 + 正则模式扫描
- **字符限制**：memory 2200, user 1375, episodic 无限制

### 删除的模块文件

- `test/test_embedding.py`
- `test/test_retrieval.py`
- `test/test_vector.py`

---

## 重要：MemoryProvider/MemoryManager 框架 + Holographic 外部提供者

### 新增文件

| 文件 | 说明 |
|------|------|
| `memory/provider.py` | MemoryProvider ABC — 可插拔记忆提供者契约 |
| `memory/manager.py` | MemoryManager — 内置/外部提供者编排器，异常隔离 |
| `memory/providers/builtin.py` | BuiltinMemoryProvider — 封装 MemoryStore，导出 MEMORY_SCHEMA |
| `memory/providers/holographic/__init__.py` | HolographicMemoryProvider — SQLite 事实存储 + HRR 语义检索 |
| `memory/providers/holographic/store.py` | SQLite 事实存储，FTS5 全文检索 + 实体解析 + 信任评分 |
| `memory/providers/holographic/retrieval.py` | 混合检索器：FTS5 + Jaccard + HRR 组合评分 |
| `memory/providers/holographic/holographic.py` | 相位向量 HRR 代数：bind/unbind/bundle/similarity |

### 删除文件

| 文件 | 说明 |
|------|------|
| `tool/builtins/memory.py` | 旧的自注册 memory 工具，由 BuiltinMemoryProvider 替代 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent/cli.py` | 移除 MemoryStore 直连 + module-level `_store` 注入，改为 MemoryManager + BuiltinMemoryProvider 模式；添加 `--holographic` 标志 |
| `agent/loop.py` | 工具列表构建合并 memory_manager schemas；dispatch 优先路由到 memory_manager；添加 MemoryManager import |
| `tool/toolsets.py` | 移除 `memories` 工具集（memory 工具不再经 registry） |
| `tool/builtins/__init__.py` | 移除 `import tool.builtins.memory` |

### 更新测试

| 文件 | 改动 |
|------|------|
| `test/test_builtins.py` | 移除 `TestMemoryTool` 类（handler + module-level `_store` 模式已删除） |
| `test/test_cli.py` | `clean_registry` 不再注册 memory 工具；`test_full_wiring` 改用 MemoryManager + handle_tool_call；`test_tool_names_after_wiring` 移除 memory 断言 |
| `test/test_toolsets.py` | 移除 `test_memory_tools_all_registered` |

### 架构要点

- **MemoryProvider**(ABC)：`name`/`is_available()` 必须实现，`system_prompt_block`/`prefetch`/`sync_turn`/`get_tool_schemas`/`handle_tool_call` 可选重写
- **MemoryManager**：内置提供者始终在首位不可移除，仅允许一个外部提供者；所有方法调用异常隔离（一个失败不阻塞其他）
- **BuiltinMemoryProvider**：始终存在，封装 MemoryStore 的文件快照模式，system_prompt + memory 工具
- **HolographicMemoryProvider**：通过 `--holographic` CLI 标志启用，提供 `fact_store`（9 种动作）+ `fact_feedback` 两个工具，无需 numpy（HRR 回退到 FTS5）
