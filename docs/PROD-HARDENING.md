# chips 生产级优化计划

> 分支: `feature/prod-hardening` | 基干: `main`
> 目标: 将 chips 从"日常可用"提升到"生产可运行"的安全、可靠、可运维水平

---

## 三层优先级

| 层级 | 定级标准 | 若不修复 |
|------|---------|---------|
| **🔴 P0** | 上线即出事 — 安全漏洞、认证缺失 | 外部攻击面暴露、数据泄露 |
| **🟠 P1** | 长期运行必出问题 — 单点故障、资源泄漏 | 服务不可用、数据丢失 |
| **🟡 P2** | 运维成本高 — 缺乏可观测性、无部署能力 | 无法排障、交付门槛高 |

---

## 🔴 P0 — 安全加固（最优先）

### P0-1 Web 默认凭据与密钥

| 模块 | 现状 | 目标 |
|------|------|------|
| `web/server.py` | JWT 密钥 fallback `"change-me-in-production"` | 启动时若未设置 `CHIPS_WEB_SECRET`，生成随机密钥并打印警告 |
| `web/server.py` | 默认账号 `admin/admin` | 首次启动强制要求设置密码，或从 env 读取 `CHIPS_WEB_PASSWORD` |
| `web/server.py` | CORS `allow_origins=["*"]` | 通过配置控制，默认禁止跨站 |

### P0-2 提示注入防御强化

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/prompt.py:_scan_injection` | 仅中英文正则匹配 | 新增编码变体检测（Base64、Unicode 转义、零宽字符） |
| `agent/prompt.py` | 无输出护栏 | 新增 `_check_output_safety(content) -> bool`，拦截明显有害回复 |
| `agent/loop.py` | 上下文文件无大小限制 | `search_context_files` 设置 64KB 上限，超限跳过并告警 |

### P0-3 插件沙箱

| 模块 | 现状 | 目标 |
|------|------|------|
| `plugins/manager.py` | `importlib` 直接加载，无权限控制 | 插件声明 `PluginManifest`（依赖、权限、入口），加载时校验 |
| `plugins/protocol.py` | 无签名校验 | 可选签名验证（`ed25519`），防篡改 |

---

## 🟠 P1 — 可靠性加固

### P1-1 多 Provider 支持与容错

| 模块 | 现状 | 目标 |
|------|------|------|
| `gateway/providers/` | 仅 `OpenAIProvider` | 新增 `AnthropicProvider`、`GeminiProvider`，统一 `ModelGateway` 协议 |
| `gateway/protocol.py` | 无自动 fallback | `FallbackGateway` 包装器：主 provider 失败→次 provider，按 priority 链式降级 |
| `gateway/rate_limit.py` | `TokenBucket` 已定义未接入 | `RateLimitedGateway` 包装器，在 provider 外统一限流 |
| `gateway/providers/openai.py` | 无熔断器 | 接入 `CircuitBreaker`：连续 N 次失败后暂停 M 秒 |
| `gateway/providers/openai.py` | HTTP 超时未配置 | 显式设置 `timeout=60`，区分连接超时和读取超时 |

### P1-2 Web 多会话隔离

| 模块 | 现状 | 目标 |
|------|------|------|
| `web/server.py` | 全局 `_agent` 单例 | `SessionManager` 管理多 agent 实例，每个会话独立 |
| `web/server.py` | 首请求阻塞初始化 | 启动时异步预初始化，或懒加载 + loading 态 |
| `web/server.py` | 无请求级别限制 | 接入 `TokenBucket`：每用户 QPS 限制，chat 接口消息长度上限 |

### P1-3 SQLite 持久化加固

| 模块 | 现状 | 目标 |
|------|------|------|
| `session/db.py` | 无 schema 版本 | 新增 `_schema_version` 表 + 迁移函数 |
| `session/db.py` | 无数据保留策略 | 配置化 `retention_days`，自动清理过期 session |
| `session/db.py` | 无 `created_at` 索引 | 增加索引，`list_sessions` 不再全表扫描 |
| `safety/audit.py` | 审计日志不清理 | 新增 `prune(before)` 方法，配置保留期限 |
| `session/db.py` | 无 VACUUM | 定期 `PRAGMA auto_vacuum=INCREMENTAL` + 手动 VACUUM |

### P1-4 上下文压缩 Token 化

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py:_maybe_trim_context` | 100K 字符阈值 | 使用 `tiktoken` 按 token 数精确计算 |
| `agent/prompt.py` | `max_prompt_chars` 字符限制 | 改为 `max_prompt_tokens`，通过 tokenizer 计算 |

### P1-5 运行时护栏

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py` | 无对话超时 | 新增 `max_conversation_seconds`：超过则中断回复 |
| `agent/loop.py` | 无 token 预算中断 | 新增 `max_response_tokens`：达到上限时截断并提示 |
| `tool/registry.py` | 工具执行无超时 | 新增 `tool_timeout` 配置，异步工具 `asyncio.wait_for()` |
| `tool/registry.py:dispatch` | `asyncio.run()` 在 Web 中会崩溃 | 检测已有事件循环，改用 `asyncio.create_task` 或线程池 |

---

## 🟡 P2 — 可运维性

### P2-1 可观测性

| 模块 | 现状 | 目标 |
|------|------|------|
| `web/server.py` | 无 metrics 端点 | 新增 `/api/metrics`：token 消耗、请求数、错误率、平均延迟 |
| `gateway/stats.py` | 仅记录 token/cost | 扩展记录：每次请求耗时、model、status_code |
| `agent/logger.py` | 分散日志 | 统一结构化日志（JSON lines），含 session_id、turn_number、事件类型 |
| `memory/` / `session/` | 无健康检查 | `GET /api/health` 返回各子系统状态 |

### P2-2 部署能力

| 模块 | 现状 | 目标 |
|------|------|------|
| 根目录 | 无 Dockerfile | 单阶段 Dockerfile，uv 安装依赖 + 启动 web |
| 根目录 | 无编排配置 | `docker-compose.yml`：chips-web + SQLite 持久化卷 |
| `web/server.py` | 纯 HTTP | 可选 TLS（读取 `CHIPS_TLS_CERT` / `CHIPS_TLS_KEY`）或建议前置反向代理 |
| `.env.example` | 不存在 | 创建环境变量模板 |

### P2-3 工具系统安全

| 模块 | 现状 | 目标 |
|------|------|------|
| `tool/registry.py:dispatch` | 参数不校验 | 接入 `jsonschema.validate()`，LLM 传参错误时返回友好错误 |
| `tool/registry.py` | 无工具级权限 | `ToolDef.required_permission` 字段 + `PermissionManager` 拦截 |
| `gateway/providers/openai.py` | `BasicError` 死代码重复 | 清理重复的 `except BadRequestError` |

### P2-4 配置管理

| 模块 | 现状 | 目标 |
|------|------|------|
| `config/store.py` | 任何 key/value 都接受 | `ConfigSchema` 定义合法 key、类型、取值范围 |
| `config/store.py` | 明文存密钥 | 新增 `encrypt_value()` / `decrypt_value()`（Fernet） |
| `config/store.py` | 单层配置 | 支持 `{chips_dir}/config.yaml` → `{project_dir}/.chips/config.yaml` 级联覆盖 |

### P2-5 记忆系统

| 模块 | 现状 | 目标 |
|------|------|------|
| `memory/providers/holographic/` | 无事实衰减 | 实现 `temporal_decay_half_life` 在 prefetch 中调低旧事实验证分 |
| `memory/providers/holographic/` | 无去重 | 语义相似度合并：`cosine_sim > 0.92` 视为重复 |
| `memory/providers/builtin.py` | 全量 prefect | 引入 LRU 缓存命中检查，避免每轮都读文件 |

---

## 实施顺序

```
P0-1 (Web安全) → P0-2 (注入防御) → P0-3 (插件沙箱)
      ↓
P1-1 (多Provider) → P1-2 (多会话) → P1-3 (持久化)
      ↓
P1-5 (运行时护栏) → P1-4 (token化压缩)
      ↓
P2-1 (可观测性) → P2-2 (部署) → P2-3 (工具安全) → P2-4 (配置) → P2-5 (记忆)
```

**原则**：前一个 P0 完成后才能开始 P1，同一层内可按依赖顺序串行或独立项并行。
Docker 环境相关项已排除，全部以 LocalEnvironment + 可选沙箱为目标。
