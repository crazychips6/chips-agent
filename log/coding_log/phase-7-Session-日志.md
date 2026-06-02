## 重要：Session 持久化（session/db.py）

- SQLite WAL 模式 + FTS5 全文搜索（content-sync trigger 自动同步）
- `create_session()` / `get_history()` / `save_message()` / `save_messages()` / `list_sessions()` / `search()` / `delete_session()`
- `save_messages()` 用 `BEGIN` + connection context manager 保证多条写入在同一个事务内（原子）
- `save_message()` 和 `delete_session()` 同理
- 会话 ID 格式：`YYYYMMDD-HHMMSS-XXXX`（时间戳+随机后缀）

## 普通：日志系统（agent/logger.py）

- `setup_logging()` — RotatingFileHandler（5MB 旋转，保留 3 份）
- `SessionFilter` — 自动注入 `session_id` 到每条日志记录
- `RedactingFormatter` 复用自 `safety/sanitize`，密钥自动脱敏

## 普通：loop.py 集成 Session 持久化

- AIAgent 新增 `session_db`、`session_id`、`_saved_count` 属性
- `_save_pending()` 增量持久化尚未写入的消息
- 在 user message、每个 tool_call 轮次、最终回复、最大迭代超限时自动保存

## 普通：cli.py 新增 --resume 参数

- `--resume`（无参）：自动恢复最近会话
- `--resume <session_id>`：恢复指定会话
- `SessionDB` 在 cli.py 创建并注入 agent
- 日志在启动时初始化

## 细微：test_session.py

- 19 条测试覆盖全部 CRUD 操作 + FTS5 搜索 + 边界情况
- 使用临时文件避免污染持久化数据
