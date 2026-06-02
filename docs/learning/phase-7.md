# Phase 7 — Session 持久化 + 日志

## 个人梳理

这次改动做了两个内容：**logging** 和 **session DB**。

### Logging（日志系统）

- 在本地记录 agent 日志（文件写到 `log/chips.log`）
- 使用了 `safety/sanitize` 的凭证剥离（RedactingFormatter）
- 旋转日志：5MB 切割，保留 3 份，最多 15MB
- 目前只搭好了架子，还没有在实际逻辑中加日志调用

### Session DB（会话持久化）

- 零依赖模块（`session/db.py`）
- SQLite + WAL 模式 + FTS5 全文搜索
- 提供完整 CRUD：create_session、save_message(save_messages)、get_history、list_sessions、search、delete_session

### Wiring 和集成

- cli.py 负责 session 的创建和恢复，以及日志初始化
  - `--resume` 有参数 → 加载指定 session
  - `--resume` 无参数 → 自动恢复最近会话
  - 没有 `--resume` → 创建新会话
- loop 中的 `_save_pending()` 在 user 提问后、每轮 tool call 结束后、收到最终回复时、最大迭代超限兜底，向 SQLite 增量持久化消息
