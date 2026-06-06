## 重要：日志系统重构 — 结构化 JSON + 模块路由 + LogManager 单例

### 问题根因
原多文件日志方案通过 root logger + `logging.Filter(name=prefix)` 实现模块分流。两个问题：
1. root logger 默认 level=WARNING，INFO 消息被静默拦截（虽有 `root.setLevel()` 修复，但仅在 `setup_logging` 执行后生效，且与第三方库日志混合）
2. `if logger.handlers: return logger` 守卫导致 `setup_logging()` 重复调用时无法重建 handlers，`uv tool install` 的冻结代码场景下无法生效

### 新架构（agent/logger.py 完全重写）

```
LogManager (单例)                JSON 文件 (chips.log)
  └── chips logger ───────────→  JSON 文件 (memory.log)  ← PrefixFilter("chips.memory")
       (setLevel=INFO)            JSON 文件 (tool.log)    ← PrefixFilter("chips.tool")
                                  JSON 文件 (session.log) ← PrefixFilter("chips.session")
                                  JSON 文件 (safety.log)  ← PrefixFilter("chips.safety")
                                  JSON 文件 (config.log)  ← PrefixFilter("chips.config")
                                  StreamHandler (stderr)  ← ConsoleFormatter
```

### 关键变更

| 文件 | 变化 |
|------|------|
| `agent/logger.py` | 完全重写：新增 `LogManager` / `JSONFormatter` / `ConsoleFormatter` / `PrefixFilter`，删除旧 `setup_logging` / `_make_handler` / `SessionFilter`（重写） |
| `agent/loop.py` | `"chips"` → `"chips.agent.loop"` |
| `tool/registry.py` | `__name__` → `"chips.tool.registry"` |
| `safety/approval.py` | `__name__` → `"chips.safety.approval"` |
| `memory/manager.py` | `__name__` → `"chips.memory.manager"` |
| `memory/providers/holographic/__init__.py` | `__name__` → `"chips.memory.holographic"` |
| `memory/providers/holographic/holographic.py` | `__name__` → `"chips.memory.holographic.hrr"` |

### 设计决策

1. **JSON 文件 + 紧凑控制台**：文件输出 JSON lines，可被 filebeat/loki 等日志收集系统直接消费；控制台输出紧凑人类可读格式
2. **PrefixFilter**：取代 `logging.Filter(name=prefix)` 的 startswith 语义，改用 `== prefix or .startswith(prefix + ".")` 避免 `chips.toolbox` 误匹配 `chips.tool`
3. **无 root logger 介入**：所有 handlers 挂在 chips logger 上，不受 root level 影响，也不污染第三方库日志
4. **无 stale handler 守卫**：`_teardown()` 关闭并移除旧 handlers，`setup()` 可多次调用
5. **脱敏集成**：直接调用 `safety.sanitize.redact()` 脱敏 JSON 字段和控制台输出
6. **零域依赖**：`tool/` / `safety/` / `memory/` 模块仅改 logger name 字面量，无新增 import

### 验证
- 所有 406 测试通过
- 手动验证：chips.log 全量、tool.log 仅 chips.tool.*、脱敏生效
