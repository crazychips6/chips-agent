---
name: observability-p0-plan
description: 可观测性 P0 三阶段计划 — Insights 引擎 → Prometheus 端点 → 告警框架
metadata:
  type: project
---

在 `feature/prod-hardening` 分支上制定可观测性 P0 计划，按效果降序：

1. **Insights 引擎** — `agent/insights.py` + `tool_call_log` 表，跨会话聚合报表（费用/用量/工具健康度）
2. **Prometheus 端点** — `gateway/metrics.py` + `/metrics` 路由 + Grafana dashboard
3. **告警框架** — `plugins/alerting.py` 阈值检查 + 状态机 + 通知渠道

**核心原则：** 效果优先于改动最小；先有数据基线再设告警；不重复造轮子，参考 LiteLLM/Hermes/prometheus-fastapi-instrumentator。

**Why:** 当时最大痛点是跨会话信息盲区——不知道整体费用和系统健康度。Prometheus 和告警都依赖这个基线数据。

**How to apply:** 开发时严格按此顺序，前三步完成后评估是否进入第四步（告警框架）。参考 [[quickapp_philosophy]] 的"用户不是调试者"原则——Insights 引擎让用户自己就能看明白系统状态。
