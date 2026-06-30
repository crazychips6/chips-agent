"""意图路由配置 — 标签 → 模型通道 + 工具可见性

每个 intent 定义：
  model: "small" | "large"  — 走小模型还是大模型
  tools: list[str] | "all"  — 该通道下可见的工具组
  reply: bool               — 小模型是否直接回复（仅无工具时）
"""

from __future__ import annotations

from typing import Literal

RouteConfig = dict[str, dict[str, object]]

# 白名单标签 → 走小模型
SMALL_MODEL_INTENTS = {"greeting", "simple_qa", "web_search", "simple_coding"}

# 黑名单工具：如果 predicted_tools 包含这些 → 强制走大模型
COMPLEX_TOOL_TRIGGERS = {"orchestrate", "sub_agent"}

# 路由表
INTENT_ROUTES: RouteConfig = {
    # 小模型通道 — 无工具，直接回复
    "greeting": {"model": "small", "tools": [], "reply": True},
    "simple_qa": {"model": "small", "tools": [], "reply": True},
    # 小模型通道 — 有工具
    "web_search": {"model": "small", "tools": ["web"], "reply": False},
    "simple_coding": {"model": "small", "tools": ["bash", "file"], "reply": False},
    # 大模型通道
    "complex": {"model": "large", "tools": "all", "reply": False},
    "delegate": {"model": "large", "tools": "all", "reply": False},
    "other": {"model": "large", "tools": "all", "reply": False},
}


def classify_route(intent: str, predicted_tools: list[str]) -> tuple[str, str]:
    """根据 intent 和 predicted_tools 决定走哪个通道。

    Returns:
        (channel, reason) — ("small"/"large", 决策原因)
    """
    # 黑名单检查：如果预测的工具包含复杂工具 → 走大模型
    if any(t in COMPLEX_TOOL_TRIGGERS for t in predicted_tools):
        return ("large", f"complex_tool:{','.join(COMPLEX_TOOL_TRIGGERS & set(predicted_tools))}")

    route = INTENT_ROUTES.get(intent, INTENT_ROUTES["other"])
    channel = route["model"]  # type: ignore[return-value]
    if channel == "small" and route.get("reply"):
        reason = f"intent:{intent}/direct"
    elif channel == "small":
        reason = f"intent:{intent}/tools:{route.get('tools', 'none')}"
    else:
        reason = f"intent:{intent}/default"
    return (channel, reason)  # type: ignore[return-value]


def get_route_config(intent: str) -> dict:
    """获取 intent 的路由配置。"""
    return INTENT_ROUTES.get(intent, INTENT_ROUTES["other"])


def should_reply_direct(intent: str) -> bool:
    """是否由小模型直接回复（无工具 ReAct）。"""
    route = INTENT_ROUTES.get(intent)
    if route is None:
        return False
    return bool(route.get("reply"))


def get_tools_for_intent(intent: str) -> list[str] | str:
    """获取 intent 对应的可见工具列表。"""
    route = INTENT_ROUTES.get(intent, INTENT_ROUTES["other"])
    return route["tools"]  # type: ignore[return-value]
