"""意图路由配置 — 标签 → 模型通道 + 工具可见性

每个 intent 定义：
  model: "small" | "large"  — 走小模型还是大模型
  tools: list[str] | "all"  — 该通道下可见的工具组

小模型走完整 ReAct（共享同一套 system prompt），
只是调用不同的模型 API。
"""

from __future__ import annotations

from typing import Literal

RouteConfig = dict[str, dict[str, object]]

# 黑名单工具：如果 predicted_tools 包含这些 → 强制走大模型
COMPLEX_TOOL_TRIGGERS = {"orchestrate", "sub_agent"}

# 路由表
INTENT_ROUTES: RouteConfig = {
    # 小模型通道（通过 Ollama provider 执行完整 ReAct）
    "greeting": {"model": "small", "tools": []},
    "simple_qa": {"model": "small", "tools": []},
    # 小模型通道 — 帯工具
    "web_search": {"model": "small", "tools": ["web"]},
    "simple_coding": {"model": "small", "tools": ["bash", "file"]},
    # 大模型通道
    "complex": {"model": "large", "tools": "all"},
    "delegate": {"model": "large", "tools": "all"},
    "other": {"model": "large", "tools": "all"},
}


def get_tools_for_intent(intent: str) -> list[str] | str:
    """获取 intent 对应的可见工具列表。"""
    route = INTENT_ROUTES.get(intent, INTENT_ROUTES["other"])
    return route["tools"]  # type: ignore[return-value]


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
    reason = f"intent:{intent}/{'small' if channel == 'small' else 'default'}"
    return (channel, reason)  # type: ignore[return-value]


def get_route_config(intent: str) -> dict:
    """获取 intent 的路由配置。"""
    return INTENT_ROUTES.get(intent, INTENT_ROUTES["other"])
