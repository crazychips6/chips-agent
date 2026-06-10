"""delegate_task 工具 — 多 Agent 任务委派

Phase 1: Agent-as-Tool 模式。
子 Agent 共享主 Agent 的 gateway + registry，独立 context、独立消息历史。

使用：
  delegate_task({
      "task": "用 web_search 搜索 xxx 并整理报告",
      "tools": ["web"],           # 工具集名列表
      "model": "deepseek-chat",   # 可选，默认同主 Agent
      "max_iterations": 10,       # 可选，默认 10
      "context": "...",           # 可选，注入子 Agent system prompt
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

# 由 cli.py 在启动时通过 wire_parent() 注入
_parent: AIAgent | None = None


def wire_parent(agent: AIAgent) -> None:
    global _parent
    _parent = agent


def _handle(args: dict[str, Any]) -> str:
    parent = _parent
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    task = args.get("task", "")
    if not task:
        return json.dumps({"error": "task 不能为空"})

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
        # 默认：继承主 Agent 的工具集
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
        logger.info("sub_agent task=%r model=%s tools=%s iter=%d",
                     task[:80], model, tool_names, max_iterations)
        result = sub.run_conversation(task, max_iterations=max_iterations)
        logger.info("sub_agent done len=%d", len(result))
        return result
    except Exception as e:
        logger.error("sub_agent failed: %s", e, exc_info=True)
        return json.dumps({"error": f"子任务执行失败: {e}"})


# ── Schema & 注册 ──

DELEGATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delegate_task",
        "description": (
            "将子任务委派给一个独立的子 Agent 执行，返回执行结果。\n\n"
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
            "required": ["task"],
        },
    },
}

registry.register(
    name="delegate_task",
    toolset="core",
    schema=DELEGATE_SCHEMA,
    handler=_handle,
)
