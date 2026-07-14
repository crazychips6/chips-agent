"""工厂函数（供 orchestrate 使用）— Agent 配置解析与子 Agent 构建

子 Agent 共享主 Agent 的 gateway + registry，独立 context、独立消息历史。

导出给 orchestrate_tool 使用：
  resolve_agent_config()
  build_sub_agent()
  get_parent()

delegate_task 工具已合并到 orchestrate（mode="single"），
不再独立注册。
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


# 子 Agent 禁止使用的工具集
_SUB_AGENT_FORBIDDEN = frozenset({"orchestrate", "sub_agent", "clarify"})


def _resolve_registry_tools(agent_name: str) -> set[str] | None:
    """从 AgentRegistry 查询角色的预设工具列表（若有 registry 且角色存在）。"""
    if not agent_name or not _registry:
        return None
    entry = _registry.get(agent_name)
    if not entry:
        return None
    raw = entry.get("tools")
    if not raw:
        return None
    from tool.toolsets import resolve_multiple_toolsets
    resolved = resolve_multiple_toolsets(raw)
    return set(resolved) if resolved else None


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
    """创建并配置子 Agent，返回 (sub_agent, final_task, max_iterations)。

    原则（LangGraph 风格）：
    - 系统提示词：不继承，子 Agent 有独立的最小 prompt
    - 历史消息：不传原始长对话，只传 goal
    - 工具集：按需分配，默认空，必须由主 Agent 显式指定
    - 禁止使用：orchestrate（不能创建子 Agent）、clarify（不能反问用户）
    """
    from agent.loop import AIAgent

    sub = AIAgent(
        model=model,
        gateway=parent.gateway,
        verbose=False,
    )
    sub.registry = parent.registry
    sub.memory_manager = parent.memory_manager
    # 设置目标，子 Agent 使用最小 prompt（不含主 Agent 的完整身份）
    sub.goal = task

    # 工具集：显式指定 > AgentRegistry 预设 > 空
    if tools:
        from tool.toolsets import resolve_multiple_toolsets
        resolved = resolve_multiple_toolsets(tools)
        sub.tool_names = (set(resolved) & parent.registry.tool_names) - _SUB_AGENT_FORBIDDEN
    else:
        # 没有显式指定 → 查 AgentRegistry 是否有该角色的预设工具
        registry_tools = _resolve_registry_tools(agent_name)
        if registry_tools:
            sub.tool_names = registry_tools - _SUB_AGENT_FORBIDDEN
        else:
            sub.tool_names = parent.tool_names - _SUB_AGENT_FORBIDDEN

    # 不继承主 Agent 的 context_files（子 Agent 有独立目标）
    # 不传原始长对话，context 参数作为额外上下文拼入 goal
    final_task = task
    if context:
        final_task = f"{task}\n\n附加上下文：\n{context}"

    sub.session_db = session_db or parent.session_db
    if sub.session_db and session_id:
        sub.session_id = sub.session_db.create_session(
            parent_session_id=session_id,
        )

    return sub, final_task, max_iterations


# delegate_task handler + schema 已移至 orchestrate（mode="single"）
# 此文件仅保留工厂函数供 orchestrate 使用
