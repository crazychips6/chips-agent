"""decompose 工具 — 任务分解与多 Agent 协作执行

注册在 sub_agent toolset，和 list_sub_agents / get_sub_agent_result 同级。
LLM 通过 `toolset enable sub_agent` 激活后可用。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tool.registry import registry
from tool.builtins.agent_tools import get_parent

logger = logging.getLogger("chips.tool.decompose_tool")


def _handle(args: dict[str, Any]) -> str:
    parent = get_parent()
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    task = args.get("task", "")
    if not task:
        return json.dumps({"error": "task 不能为空"})

    agents = args.get("agents") or None  # None = 使用 registry 全部角色

    try:
        from agent.decomposer import decompose_and_execute
        result = decompose_and_execute(task=task, parent=parent, agents=agents)
        return result
    except Exception as e:
        logger.error("decompose_failed: %s", e, exc_info=True)
        return json.dumps({"error": f"任务分解执行失败: {e}"})


DECOMPOSE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "decompose",
        "description": (
            "将复杂任务自动分解为多个子任务，分配给合适的 Agent 角色并发/串行执行，"
            "最后汇总结果。适用于跨领域、多步骤的复杂任务。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要分解执行的复杂任务描述，越详细越好",
                },
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，指定可用 Agent 角色列表（如 ['researcher', 'coder']）。"
                                   "不指定则使用所有已注册角色。",
                },
            },
            "required": ["task"],
        },
    },
}

registry.register(
    name="decompose",
    toolset="sub_agent",
    schema=DECOMPOSE_SCHEMA,
    handler=_handle,
)
