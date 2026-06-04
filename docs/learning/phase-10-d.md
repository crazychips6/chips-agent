# Phase 10 D 学习笔记

## 审计日志：SQLite 与文本 Log 的分工

### 文本 Log（chips.log）

- **给人看的**：一行行文本，`tail -f` 实时跟踪，`grep` 粗略搜索
- 无 schema，每行是自由格式字符串
- 适合运行时监控、调试排错

### 审计（audit.db — SQLite）

- **给机器查的**：结构化数据，字段独立，schema 约束
- `WHERE event_type='tool_call' AND created_at > today` 精确查询
- `GROUP BY tool ORDER BY count DESC` 做统计
- 分页、排序、聚合都方便

### SQLite 比文本文件更适合审计的原因

1. **结构化查询** — 工具名、事件类型、时间戳都是独立字段，不需要逐行 parse
2. **哈希链防篡改** — 依赖 id 和 prev_hash 的事务原子写入，文本文件做不到
3. **WAL 模式不阻塞** — 写入不影响读取，审计不干扰主流程

### 一句话总结

Log 给人看，SQLite 给机器查。两者互补，不是替代关系。
