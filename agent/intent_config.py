"""意图路由配置 — 标签 → 模型通道 + 工具可见性

每个 intent 定义：
  model: "small" | "large"  — 走小模型还是大模型
  tools: list[str] | "all"  — 该通道下可见的工具组

小模型走完整 ReAct（共享同一套 system prompt），
只是调用不同的模型 API。

配置来源：agent/intents/*.yaml（新增意图只需加 YAML 文件）
"""

from __future__ import annotations

from typing import Any

from agent.intent_loader import intent_registry

# 全局开关：设为 False 关闭小模型直接回答
ENABLE_SMALL_DIRECT = True

# 黑名单工具：如果 predicted_tools 包含这些 → 强制走大模型
COMPLEX_TOOL_TRIGGERS = {"orchestrate", "sub_agent"}


# ── 兼容旧接口 ──


def get_intent_routes() -> dict[str, dict[str, Any]]:
    """获取路由表（从 YAML 配置加载）。"""
    return intent_registry.get_route_table()


# 保留 INTENT_ROUTES 变量用于兼容（某些代码可能直接引用）
INTENT_ROUTES: dict[str, dict[str, Any]] = {}


def _sync_routes():
    """同步路由表到模块级变量。"""
    global INTENT_ROUTES
    INTENT_ROUTES = intent_registry.get_route_table()


# 初始化时同步一次
_sync_routes()


def get_tools_for_intent(intent: str) -> list[str] | str:
    """获取 intent 对应的可见工具列表。"""
    return intent_registry.get_tools_for_intent(intent)


def classify_route(intent: str, predicted_tools: list[str]) -> tuple[str, str]:
    """根据 intent 和 predicted_tools 决定走哪个通道。

    Returns:
        (channel, reason) — ("small"/"large", 决策原因)
    """
    # 全局开关
    if not ENABLE_SMALL_DIRECT:
        return ("large", "global_disabled")

    # 黑名单检查：如果预测的工具包含复杂工具 → 走大模型
    if any(t in COMPLEX_TOOL_TRIGGERS for t in predicted_tools):
        return ("large", f"complex_tool:{','.join(COMPLEX_TOOL_TRIGGERS & set(predicted_tools))}")

    intent_def = intent_registry.get(intent)
    if intent_def is None:
        channel = "large"
    else:
        channel = intent_def.model
    reason = f"intent:{intent}/{'small' if channel == 'small' else 'default'}"
    return (channel, reason)


def get_route_config(intent: str) -> dict:
    """获取 intent 的路由配置。"""
    intent_def = intent_registry.get(intent)
    if intent_def is None:
        intent_def = intent_registry.get("other")
    if intent_def is None:
        return {"model": "large", "tools": "all"}
    return intent_def.to_route()
