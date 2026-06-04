## 重要：Phase 10 D1 — 持久化审批白名单

- 新增 `safety/allowlist.py`，用户批准的 dangerous 命令自动记入 `~/.chips/allowlist.yaml`
- 精确字符串匹配（不含糊），支持 check/add/remove/clear/list_all/count
- `CHIPS_ALLOWLIST_PATH` 环境变量覆盖路径，YAML 格式持久化含 pattern 名和时间戳
- `approval.check()` 集成：hardline 后、dangerous 前查白名单；用户批准自动 `allowlist_add()`

## 重要：Phase 10 D2 — 审计日志

- 新增 `safety/audit.py`，独立 SQLite DB + SHA256 哈希链（篡改可检测）
- `AuditLog` 类提供 log/query/verify/count，模块级 `log_event()` 便利函数
- `CHIPS_AUDIT_DB_PATH` 环境变量覆盖路径

## 普通：集成审计到现有模块

- `agent/loop.py`：`registry.dispatch()` 后写 `tool_call` 事件，上下文压缩 Phase 2 写 `context_trim` 事件
- `environment/local.py`：`approval.check()` 后写 `approval` 事件
- `safety/approval.py`：审批决策链各分支写 `approval` 事件（hardline/user_confirm/user_reject/non_interactive）

## 细微：测试结构改进

- `test/test_approval.py` 新增 `_no_allowlist` / `_no_audit` 全局 mock fixtures，避免测试污染真实文件
- `test/test_allowlist.py`（36 条）覆盖 CRUD、持久化、文件损坏等边界
- `test/test_audit.py`（24 条）覆盖分页查询、哈希链验证、并发安全
