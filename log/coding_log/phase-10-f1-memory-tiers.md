## 重要：Phase 10 F1 — 记忆层级扩展 (working / episodic / semantic)

### 三层模型

| 层级 | 存储 | 生命周期 | 用途 |
|------|------|---------|------|
| **Working** | 内存 dict | 当前会话 | 当前会话的结构化笔记，格式 `key: value` |
| **Episodic** | `EPISODIC.md` | 跨会话 | 历史摘要，`add()` 时自动加时间戳 |
| **Semantic** | `MEMORY.md` / `USER.md` | 永久 | 持久知识（与之前一致） |

### 修改文件

**`memory/store.py`**：
- 新增 `EPISODIC.md` 文件读写，`add(category="episodic")` 自动带时间戳追加
- 新增 `add(category="working")`，支持 `key: value` 格式的工作记忆
- 新增 `set_working()` / `get_working()` / `clear_working()` / `summarize_to_episodic()`
- `for_system_prompt()` 现在包含持久记忆 + 历史会话摘要
- `get_all()` 现在返回 memory/user/episodic/working 四键

**`tool/builtins/memory.py`**：
- category enum 扩展为 `[memory, user, episodic, working]`
- `memory_read(category="working")` 返回当前工作记忆键值对
- `memory_write(category="working")` 写入当前会话笔记
- `memory_write(category="episodic")` 保存会话摘要

**`agent/prompt.py`**：
- 从 7 层扩展到 9 层：新增"历史会话摘要"(Layer 5) 和"当前会话笔记"(Layer 6)
- `build()` 新增 `episodic` 和 `working` 参数

**`agent/loop.py`**：
- `run_conversation()` 将 episodic + working 注入到 system prompt

## 细微：测试

- `test_memory.py` 新增 `TestWorkingMemory`(8 条) 和 `TestEpisodicMemory`(7 条)
- `test_prompt.py` 更新 layer 名称，新增 episodic/working 层测试
- `test_loop.py` / `test_cli.py` 更新 layer 名称（记忆快照 → 持久记忆）
- 全量 327 条通过
