# Phase 4 — Memory 冻结快照

## 重要

- 创建 `memory/store.py`：`MemoryStore` 实现
  - 双文件模型：`MEMORY.md` + `USER.md`，按 category 分区存储
  - `for_system_prompt()` 返回冻结快照文本，注入 system prompt
  - `add(content, category)` 追加记忆并原子写回（临时文件 + os.replace）
  - 快照在 `__init__` 时冻结，`add()` 更新快照和磁盘
- 创建 `tool/builtins/memory.py`：`memory_read` / `memory_write` 工具，自注册到 `memory` 工具集
  - 模块级 `_store` 引用由 cli.py wiring 时注入
- 更新 `tool/toolsets.py`：新增 `memory` 工具集（`memory_read` + `memory_write`），`all` 包含 `memory`
- 更新 `agent/prompt.py`：`build(memory_snapshot)` 接受记忆快照并注入
- 更新 `agent/loop.py`：`AIAgent` 新增 `memory` 属性，`run_conversation` 读取快照传给 `PromptBuilder`
- 更新 `agent/cli.py`：wiring 时创建 `MemoryStore`，注入 agent 和 memory 工具模块

## 普通

- 创建 `test/test_memory.py`：8 个测试覆盖
  - 空初始化、添加读取、双分类、非法分类、多条追加
  - 跨实例持久化、原子写完整性、system prompt 格式
- 更新 `tool/builtins/__init__.py`：导入 memory 模块触发自注册
