## 重要：Phase 10 A2 — 上下文压缩保护

- 重写 `agent/loop.py:_maybe_trim_context`，解决旧实现 FIFO 逐条删除可能破坏 assistant/tool 配对的问题
- 新增 `agent/loop.py:_total_chars` 辅助方法

### 新策略：双阶段压缩

**Phase 1 — 压缩 tool 结果**（非破坏性）：
- tool 消息 content > 2000 字符时截断至 2000，追加 `...(truncated)`
- 保留全部消息结构，只缩减内容

**Phase 2 — 原子组删除**：
- 构建消息组：assistant(with tool_calls) + 紧随的 tool 消息为原子单位，单条独立消息为单独组
- 保护第 1 组（用户初始消息）和最后 2 组（最近上下文）
- 从中间最旧的组开始删除，逐组删直到长度达标
- 每次删除后重建分组（索引已变化）

### 测试

- 新增 `test/test_loop.py::TestTrimContext` 5 个测试：未超限不删、tool 压缩、中间组删除保护首尾、少量消息不删、配对完整性校验
- 全量测试从 209 → 214 条通过

### 修复的 bug

- `_total_chars` 要处理 `content: None` 的情况（assistant 消息可能 content=None 但有 tool_calls），用 `m.get("content") or ""` 兜底
