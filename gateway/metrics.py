"""Prometheus 指标定义 — chips 标准化可观测性指标

模块级定义，在 gateway/stats.py 和 agent/loop.py 中引用并更新。
所有指标通过 /metrics 端点暴露给 Prometheus 抓取。

指标命名规则: chips_{subsystem}_{name}_{unit}
参考: LiteLLM 指标分层 + prometheus_client 最佳实践
"""

from prometheus_client import Counter, Histogram, Gauge

# ── LLM 调用 ──

llm_calls_total = Counter(
    "chips_llm_calls_total",
    "Total LLM API calls",
    ["model", "provider", "status"],  # status: ok / error
)

llm_tokens_total = Counter(
    "chips_llm_tokens_total",
    "Total LLM tokens consumed",
    ["model", "token_type"],  # token_type: prompt / completion / cache_read / cache_write / reasoning
)

llm_cost_total = Counter(
    "chips_llm_cost_total",
    "Total estimated cost in USD",
    ["model"],
)

llm_duration_seconds = Histogram(
    "chips_llm_duration_seconds",
    "LLM call latency in seconds",
    ["model"],
    buckets=[.5, 1, 2, 5, 10, 30, 60],
)

llm_errors_total = Counter(
    "chips_llm_errors_total",
    "Total LLM call errors",
    ["model", "error_type"],
)

# ── 工具调用 ──

tool_calls_total = Counter(
    "chips_tool_calls_total",
    "Total tool calls",
    ["tool_name", "status"],  # status: success / error
)

tool_duration_seconds = Histogram(
    "chips_tool_duration_seconds",
    "Tool execution latency in seconds",
    ["tool_name"],
    buckets=[.1, .5, 1, 2, 5, 10, 30],
)

# ── 系统 ──

session_active = Gauge(
    "chips_session_active",
    "Number of currently active sessions",
)

deployment_state = Gauge(
    "chips_deployment_state",
    "Component health: 0=ok, 1=degraded, 2=down",
    ["component"],
)
