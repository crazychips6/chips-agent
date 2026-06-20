# 可观测性 P0 工作计划

> 日期：2026-06-20
> 分支：feature/prod-hardening
> 背景：chips 已有结构化日志、Langfuse 追踪、UsageRecorder 基础，但缺少跨会话聚合报表、标准化实时指标、主动告警

---

## 总体原则

1. **效果优先**，不追求改动最小。先做价值最大的，再做锦上添花的。
2. **不重复造轮子**，参考成熟项目的模式：
   - [LiteLLM](https://docs.litellm.ai/docs/proxy/prometheus) — 指标分层 + Callback 告警
   - [prometheus-fastapi-instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator) — FastAPI + Prometheus 标准做法
   - [Hermes](https://github.com/crazychips6/hermes/) `agent/insights.py` — SQLite 轻量聚合引擎
   - [kpi-engine](https://pypi.org/project/kpi-engine/) — 声明式 KPI DSL
3. **先有数据基线，再设告警**。Insights 和 Prometheus 产生的历史数据是告警阈值设定的依据。

---

## 执行顺序（按效果降序）

### 🥇 第一优先：Insights 引擎 — 跨会话聚合报表

**为什么效果最大：** 当下最大的信息盲区是"跑完会话后完全不知道整体情况"——花了多少钱、哪个模型烧钱、哪个工具容易挂。Insights 引擎直接回答这些最高频问题。

**设计思路（参考 Hermes `agent/insights.py`）：**

#### 1. 补数据表 — `tool_call_log`

当前 `usage_log` 只记录 LLM 调用，工具调用无持久化统计。需要新建表：

```sql
CREATE TABLE IF NOT EXISTS tool_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,              -- success / error
    duration_ms REAL,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

#### 2. 聚合引擎 `agent/insights.py`

封装以下查询，输出同时支持 `rich.table`（终端）和 `dict`（Web API）：

| 查询 | SQL 模式 | 价值 |
|------|----------|------|
| `cost_by_model(days)` | `SELECT model, SUM(cost_estimate) FROM usage_log GROUP BY model ORDER BY cost DESC` | 谁在烧钱 |
| `daily_cost_trend(days)` | `SELECT DATE(created_at), SUM(cost_estimate) ... GROUP BY 1 ORDER BY 1` | 费用走势 |
| `token_usage_by_model(days)` | `SELECT model, SUM(prompt_tokens), SUM(completion_tokens) ... GROUP BY model` | 用量分布 |
| `most_used_tools(days)` | `SELECT tool_name, COUNT(*), AVG(duration_ms) FROM tool_call_log GROUP BY tool_name` | 哪些工具最忙 |
| `tool_error_rates(days)` | `SELECT tool_name, status, COUNT(*) FROM tool_call_log GROUP BY tool_name, status` | 工具健康度 |
| `error_rate_by_model(days)` | `SELECT model, COUNT(*) FILTER(WHERE error), COUNT(*) ... GROUP BY model` | 模型稳定性 |
| `session_portrait(session_id)` | 拼接 usage_log + tool_call_log 的完整画像 | 排查对话 |
| `weekly_report()` | 综合以上，一键周报 | 管理决策 |

#### 3. 注入点

- `agent/loop.py` — 每次工具调用结束后写 `tool_call_log`
- `gateway/stats.py` — `usage_log` 已有，无需动

#### 4. Web API

```
GET /api/insights/cost-by-model?days=7
GET /api/insights/daily-cost?days=30
GET /api/insights/tool-usage?days=7
GET /api/insights/session/<id>
GET /api/insights/weekly-report
```

#### 涉及文件

| 文件 | 改动 |
|------|------|
| `session/db.py` | +`tool_call_log` 表建表 + 插入方法 + 聚合查询方法 |
| `agent/loop.py` | 工具调用处写 tool_call_log |
| `agent/insights.py` | **新建** — 聚合引擎封装 |
| `web/server.py` | +`/api/insights/*` 路由 |
| `agent/cli.py` | 可选：+`chips insight` 子命令 |

---

### 🥈 第二优先：Prometheus 端点 — 标准化实时指标

**为什么效果大：** Insights 是事后分析（跑完看报表），Prometheus 是实时监控（运行时看仪表盘）。两者互补。接上 Grafana 后团队一眼看到系统状态。

**设计思路（参考 LiteLLM 指标分层 + prometheus-fastapi-instrumentator 闭包模式）：**

#### 1. 指标定义 `gateway/metrics.py`

```python
# LLM 调用
chips_llm_calls_total          Counter  [model, provider, status]
chips_llm_tokens_total         Counter  [model, token_type]
chips_llm_cost_total           Counter  [model]
chips_llm_duration_seconds     Histogram [model]  buckets=[.5, 1, 2, 5, 10, 30, 60]
chips_llm_errors_total         Counter  [model, error_type]

# 工具调用
chips_tool_calls_total         Counter  [tool_name, status]
chips_tool_duration_seconds    Histogram [tool_name]  buckets=[.1, .5, 1, 2, 5, 10]

# 系统
chips_session_active           Gauge    —   (启停时 +/-1)
chips_deployment_state         Gauge    [component]  (0=ok, 1=degraded, 2=down)
```

#### 2. 注入点

- `gateway/stats.py` — `record()` 里同步更新 Prometheus Counter/Histogram
- `agent/loop.py` — 工具调用处更新 `chips_tool_calls_total` + `chips_tool_duration_seconds`
- 入侵最小，不改调用链路

#### 3. Web 端点 `web/server.py`

```python
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

@router.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
```

#### 4. Grafana Dashboard

附带一份 `grafana/dashboard.json`，导入即用。面板包括：
- LLM 调用 QPS + 延迟 P50/P95/P99
- Token 消耗速率
- 实时费用
- 工具调用成功率
- 错误率

#### 涉及文件

| 文件 | 改动 |
|------|------|
| `gateway/metrics.py` | **新建** — Prometheus 指标定义 |
| `gateway/stats.py` | `record()` 内同步更新指标 |
| `agent/loop.py` | 工具指标注入 |
| `web/server.py` | +`/metrics` 路由 |
| `pyproject.toml` | +`prometheus-client` 依赖 |
| `grafana/dashboard.json` | **新建** — 仪表盘配置 |

---

### 🥉 第三优先：告警框架 — 阈值触发通知

**为什么放最后：** 告警的阈值需要 Insights 的历史数据和 Prometheus 的实时数据才能合理设定。没有基线就设告警 = 猜。先有数据再说。

**设计思路（参考 LiteLLM `ProxyLogging` 的告警架构 + Langfuse Monitor 状态机）：**

#### 1. 架构

```
事件源 (LLM调用/工具调用/定时器)
    ↓
阈值检查 (duration > 30s? error_rate > 5%? cost > $5/天?)
    ↓
状态机 (ok → warning → alert → ok)  ← 防重复通知
    ↓
通知渠道 (log / webhook / Slack)
```

#### 2. 告警规则

| 告警类型 | 触发器 | 默认阈值 | 严重度 |
|---------|--------|---------|--------|
| `llm_slow_response` | 每次 LLM 调用后 | P95 > 30s | warning |
| `llm_high_error_rate` | 滑动 5min 窗口 | 错误率 > 10% | critical |
| `llm_hanging_request` | 启动协程等待 | 超 60s 未返回 | critical |
| `budget_daily` | 每日累计 | > $5.00 | warning |
| `budget_monthly` | 每月累计 | > $50.00 | critical |
| `tool_high_failure` | 滑动 5min 窗口 | 错误率 > 20% | warning |
| `session_loop` | loop.py 检测 | 触发死循环检测 | info |

#### 3. 状态机模式

```
ok ──[条件触发]──→ warning ──[持续超阈值]──→ alert ──[恢复]──→ ok
                    ↑                              │
                    └────── [阈值回降但未恢复] ──────┘
```

防止同一问题反复通知。

#### 4. 通知渠道（扩展模式）

```python
alerting.register_channel("log", LogChannel())        # 默认，记录告警到日志
alerting.register_channel("webhook", WebhookChannel(url))  # POST JSON 到指定 URL
# 可扩展：SlackChannel, EmailChannel, PagerDutyChannel
```

#### 涉及文件

| 文件 | 改动 |
|------|------|
| `plugins/alerting.py` | **新建** — 告警框架核心 |
| `plugins/protocol.py` | 可选：+告警相关 hook |
| `plugins/manager.py` | 可选：启动告警插件 |
| `agent/loop.py` | 注入告警检测点 |

---

## 总体工作量估计

| 阶段 | 涉及新文件 | 涉及改文件 | 预估代码行 |
|------|-----------|-----------|-----------|
| Insights 引擎 | `agent/insights.py` | `session/db.py`, `agent/loop.py`, `web/server.py` | ~400 行 |
| Prometheus 端点 | `gateway/metrics.py`, `grafana/dashboard.json` | `gateway/stats.py`, `agent/loop.py`, `web/server.py`, `pyproject.toml` | ~300 行 |
| 告警框架 | `plugins/alerting.py` | `agent/loop.py` | ~250 行 |
| **合计** | 4 新文件 + 1 JSON | 6 文件 | ~950 行 |

---

## 依赖关系图

```
Insights 引擎  ←── 独立，但 tool_call_log 表是 Prometheus 工具指标的数据源之一
                    │
                    ▼
Prometheus 端点  ←── 独立，可以并行做
                    │
                    ▼
告警框架          ←── 依赖 Insights 基线 + Prometheus 实时数据设定合理阈值
```

三个可以串行做也可以 Insights + Prometheus 并行，告警一定放在最后。
