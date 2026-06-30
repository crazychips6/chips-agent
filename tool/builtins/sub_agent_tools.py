"""sub_agent 工具 — 子 Agent 执行记录查询（合并 list_sub_agents / get_sub_agent_result）

使用方式：
  sub_agent({"action": "list", "agent_name": "researcher"})  — 列出子 Agent
  sub_agent({"action": "get", "record_id": "xxx"})           — 查看某次详情
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tool.registry import registry
from tool.builtins.agent_tools import get_parent

logger = logging.getLogger("chips.tool.sub_agent_tools")


def _handle(args: dict[str, Any]) -> str:
    parent = get_parent()
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    action = args.get("action", "list")

    if action == "list":
        agent_name = args.get("agent_name", "")
        records = parent.list_sub_agents(agent_name or None)
        return json.dumps({
            "total": len(records),
            "sub_agents": records,
        }, ensure_ascii=False, default=str)

    if action == "get":
        record_id = args.get("record_id", "")
        if not record_id:
            return json.dumps({"error": "record_id 不能为空"})
        result = parent.get_sub_agent_result(record_id)
        if result is None:
            return json.dumps({"error": f"未找到 record_id: {record_id}"})
        output = {
            "id": result.get("id"),
            "agent_name": result.get("agent_name"),
            "task": result.get("task"),
            "status": result.get("status"),
            "iterations": result.get("iterations", 0),
            "tool_calls": result.get("tool_calls", []),
            "prompt_tokens": result.get("prompt_tokens", 0),
            "completion_tokens": result.get("completion_tokens", 0),
            "output": result.get("output", ""),
            "error": result.get("error"),
            "started_at": result.get("started_at"),
            "completed_at": result.get("completed_at"),
        }
        return json.dumps(output, ensure_ascii=False, default=str)

    actions = "list, get"
    return json.dumps({"error": f"未知操作: {action}（支持: {actions}）"})


# ── 注册 ──

registry.register(
    name="sub_agent",
    toolset="sub_agent",
    schema={
        "type": "function",
        "function": {
            "name": "sub_agent",
            "description": "子 Agent 执行记录查询",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get"],
                        "description": "list=列出记录, get=查看详情",
                    },
                    "agent_name": {
                        "type": "string",
                        "description": "按角色名筛选（可选）",
                    },
                    "record_id": {
                        "type": "string",
                        "description": "子 Agent 记录 ID（orchestrate 返回结果中可见）",
                    },
                },
                "required": ["action"],
            },
        },
    },
    handler=_handle,
    group="agent",
    model_scope="large",
)
