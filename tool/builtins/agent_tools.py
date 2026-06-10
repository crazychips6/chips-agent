"""delegate_task 工具 — 多 Agent 任务委派

Phase 1: Agent-as-Tool 模式。
Phase 2: Agent Registry — agents.yaml 定义的角色 Agent。

子 Agent 共享主 Agent 的 gateway + registry，独立 context、独立消息历史。

使用：
  # 内联参数（Phase 1）
  delegate_task({
      "task": "用 web_search 搜索 xxx 并整理报告",
      "tools": ["web"],
      "model": "deepseek-chat",
  })

  # 注册表 Agent（Phase 2）
  delegate_task({
      "agent": "researcher",
      "task": "搜索 xxx 的最新进展",
  })
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
    """注入 AgentRegistry。"""
    global _registry
    _registry = reg


def _handle(args: dict[str, Any]) -> str:
    parent = _parent
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    task = args.get("task", "")
    if not task:
        return json.dumps({"error": "task 不能为空"})

    # ── 从 registry 或内联参数解析 ──
    model: str = parent.model
    tool_names: list[str] | None = None
    max_iterations: int = 10
    context: str = ""

    agent_name = args.get("agent", "")
    if agent_name:
        # 从 AgentRegistry 查找定义
        if _registry is None:
            return json.dumps({"error": "AgentRegistry 未初始化，无法使用 agent 参数"})
        entry = _registry.get(agent_name)
        if entry is None:
            available = ", ".join(_registry.names)
            return json.dumps({
                "error": f"未知 Agent: '{agent_name}'",
                "available_agents": available,
            })
        model = entry.get("model", parent.model)
        tool_names = entry.get("tools", None)
        max_iterations = min(entry.get("max_iterations", 10), 30)
        context = entry.get("system_prompt", "")
    else:
        # 内联参数
        model = args.get("model", parent.model)
        tool_names = args.get("tools", None)
        max_iterations = min(args.get("max_iterations", 10), 30)
        context = args.get("context", "")

    # 注入额外上下文到 task 前端
    if context:
        task = f"[上下文]\n{context}\n\n[任务]\n{task}"

    # ── 创建子 Agent ──
    from agent.loop import AIAgent

    sub = AIAgent(
        model=model,
        gateway=parent.gateway,
        verbose=False,
    )
    sub.registry = parent.registry
    sub.memory_manager = parent.memory_manager  # 共享（子 Agent 不触发写操作）

    # 解析工具集
    if tool_names:
        from tool.toolsets import resolve_multiple_toolsets

        resolved = resolve_multiple_toolsets(tool_names)
        sub.tool_names = set(resolved) & parent.registry.tool_names
        sub.enabled_toolsets = list(tool_names)
    else:
        sub.tool_names = parent.tool_names
        sub.enabled_toolsets = list(parent.enabled_toolsets)

    # 可选：子 Agent 继承上下文文件
    if parent.context_files:
        sub.context_files = parent.context_files

    # ── Session 追踪（可选） ──
    sub.session_db = parent.session_db
    if parent.session_db and parent.session_id:
        sub.session_id = parent.session_db.create_session(
            parent_session_id=parent.session_id,
        )

    # ── 执行子任务 ──
    try:
        tag = agent_name or "(inline)"
        logger.info("sub_agent[%s] task=%r model=%s tools=%s iter=%d",
                     tag, task[:80], model, tool_names, max_iterations)
        result = sub.run_conversation(task, max_iterations=max_iterations)
        logger.info("sub_agent[%s] done len=%d", tag, len(result))
        return result
    except Exception as e:
        logger.error("sub_agent[%s] failed: %s", tag, e, exc_info=True)
        return json.dumps({"error": f"子任务执行失败: {e}"})


# ── Schema & 注册 ──

DELEGATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delegate_task",
        "description": (
            "将子任务委派给一个独立的子 Agent 执行，返回执行结果。\n\n"
            "两种使用方式：\n\n"
            "1. 注册表 Agent（推荐）：\n"
            '   delegate_task({"agent": "researcher", "task": "搜索xxx"})\n'
            "   通过 agent 参数引用 agents.yaml 中定义的角色 Agent，"
            "自动使用预设的模型、工具集和系统提示。\n\n"
            "2. 内联参数：\n"
            '   delegate_task({"task": "...", "tools": ["web"], "model": "..."})\n'
            "   手动指定所有参数。\n\n"
            "适用场景：\n"
            "- 需要大量搜索、阅读文档等耗时操作，不影响主 Agent 的对话状态\n"
            "- 需要使用特定的工具组合来完成专项任务\n"
            "- 子任务有明确边界，完成后只需拿结果\n\n"
            "子 Agent 是独立的 ReAct 循环，有自己的消息历史。"
            "执行完毕返回文本结果，主 Agent 继续。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "agents.yaml 中定义的角色 Agent 名，如 'researcher', 'coder'。"
                                   "使用后自动填充 tools/model/context 等参数。",
                },
                "task": {
                    "type": "string",
                    "description": "给子 Agent 的任务描述。尽量清晰完整，包含背景、目标、输出格式要求。",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "子 Agent 可用的工具集名数组，如 ['web', 'file']。不指定则继承主 Agent 的工具集。",
                },
                "model": {
                    "type": "string",
                    "description": "子 Agent 使用的模型名，不指定则与主 Agent 相同。",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "子 Agent 的最大迭代次数，默认 10，最大 30。",
                },
                "context": {
                    "type": "string",
                    "description": "额外上下文信息，注入到子 Agent 的 system prompt 中（可选）。",
                },
            },
            "oneOf": [
                {"required": ["agent", "task"]},
                {"required": ["task"]},
            ],
        },
    },
}

registry.register(
    name="delegate_task",
    toolset="core",
    schema=DELEGATE_SCHEMA,
    handler=_handle,
)
