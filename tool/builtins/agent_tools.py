"""delegate_task 工具 — 多 Agent 任务委派

Phase 1: Agent-as-Tool 模式。
Phase 2: Agent Registry — agents.yaml 定义的角色 Agent。

子 Agent 共享主 Agent 的 gateway + registry，独立 context、独立消息历史。

提供 expose 给 orchestrate_tool 使用的工厂函数：
  resolve_agent_config()
  build_sub_agent()
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.loop import AIAgent

from tool.registry import registry

logger = logging.getLogger("chips.tool.agent_tools")

# 由 cli.py 在启动时通过 wire_parent() / wire_registry() 注入
_parent: AIAgent | None = None
_registry: Any = None  # config.agent_config.AgentRegistry


def wire_parent(agent: AIAgent) -> None:
    global _parent
    _parent = agent


def wire_registry(reg: Any) -> None:
    global _registry
    _registry = reg


def get_parent() -> AIAgent | None:
    return _parent


def get_registry() -> Any:
    return _registry


# ── 工厂函数（供 delegate_task / orchestrate 共享） ──


def resolve_agent_config(
    args: dict[str, Any],
    parent: AIAgent,
) -> dict[str, Any]:
    """解析 Agent 配置：从 registry 或内联参数提取 model/tools/context 等。

    返回 dict 包含：model, tools, max_iterations, context, agent_name
    """
    model: str = parent.model
    tool_names: list[str] | None = None
    max_iterations: int = 10
    context: str = ""
    agent_name: str = ""

    name = args.get("agent", "")
    if name:
        if _registry is None:
            raise RuntimeError("AgentRegistry 未初始化，无法使用 agent 参数")
        entry = _registry.get(name)
        if entry is None:
            available = ", ".join(_registry.names)
            raise ValueError(f"未知 Agent: '{name}'，可用: {available}")
        model = entry.get("model", parent.model)
        tool_names = entry.get("tools", None)
        max_iterations = min(entry.get("max_iterations", 10), 30)
        context = entry.get("system_prompt", "")
        pool_size = entry.get("pool_size", None)
        agent_name = name
    else:
        model = args.get("model", parent.model)
        tool_names = args.get("tools", None)
        max_iterations = min(args.get("max_iterations", 10), 30)
        context = args.get("context", "")
        pool_size = None

    return {
        "model": model,
        "tools": tool_names,
        "max_iterations": max_iterations,
        "context": context,
        "agent_name": agent_name,
        "pool_size": pool_size,
    }


def build_sub_agent(
    task: str,
    parent: AIAgent,
    *,
    model: str,
    tools: list[str] | None = None,
    max_iterations: int = 10,
    context: str = "",
    agent_name: str = "",
    pool_size: int | None = None,  # noqa: ARG001 — used by orchestrate for pool control
    session_db=None,
    session_id: str = "",
) -> tuple[AIAgent, str, int]:
    """创建并配置子 Agent，返回 (sub_agent, final_task, max_iterations)。"""
    if context:
        final_task = f"[上下文]\n{context}\n\n[任务]\n{task}"
    else:
        final_task = task

    from agent.loop import AIAgent

    sub = AIAgent(
        model=model,
        gateway=parent.gateway,
        verbose=False,
    )
    sub.registry = parent.registry
    sub.memory_manager = parent.memory_manager

    if tools:
        from tool.toolsets import resolve_multiple_toolsets
        resolved = resolve_multiple_toolsets(tools)
        sub.tool_names = set(resolved) & parent.registry.tool_names
        sub.permanent_toolsets = list(tools)
    else:
        sub.tool_names = parent.tool_names
        sub.permanent_toolsets = list(parent.permanent_toolsets)

    if parent.context_files:
        sub.context_files = parent.context_files

    sub.session_db = session_db or parent.session_db
    if sub.session_db and session_id:
        sub.session_id = sub.session_db.create_session()

    return sub, final_task, max_iterations


# ── Handler 已移至 orchestrate_tool.py（mode="single"）
# 保留工厂函数供 orchestrate_tool.py 使用
