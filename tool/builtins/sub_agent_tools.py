"""list_sub_agents + get_sub_agent_result — 子 Agent 执行记录查询工具

条件式暴露：仅在当前对话中有过子 Agent 执行时，LLM 才能看到这两个工具。
注册在 \"sub_agent\" toolset 中，不随 core 常驻。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tool.registry import registry
from tool.builtins.agent_tools import get_parent

logger = logging.getLogger("chips.tool.sub_agent_tools")


def _handle_list(args: dict[str, Any]) -> str:
    """列出子 Agent 执行记录。"""
    parent = get_parent()
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    agent_name = args.get("agent_name", "")
    records = parent.list_sub_agents(agent_name or None)
    return json.dumps({
        "total": len(records),
        "sub_agents": records,
    }, ensure_ascii=False, default=str)


def _handle_get_result(args: dict[str, Any]) -> str:
    """获取子 Agent 完整执行结果。"""
    parent = get_parent()
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    record_id = args.get("record_id", "")
    if not record_id:
        return json.dumps({"error": "record_id 不能为空"})

    result = parent.get_sub_agent_result(record_id)
    if result is None:
        return json.dumps({"error": f"未找到 record_id: {record_id}"})

    # 结果可能包含大量 messages，只截取摘要字段方便 LLM 阅读
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


# ── Schema & 注册 ──

LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_sub_agents",
        "description": "列出当前对话中所有子 Agent 的执行记录（不含完整消息内容），"
                       "可指定 agent_name 筛选某角色的历史记录。",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "可选，按角色名筛选（如 researcher、coder）",
                },
            },
        },
    },
}

GET_RESULT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_sub_agent_result",
        "description": "获取某次子 Agent 执行的完整详情，包括工具调用链、迭代次数、token 消耗等。",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "子 Agent 记录 ID（在 delegate_task / orchestrate 返回结果中可见）",
                },
            },
            "required": ["record_id"],
        },
    },
}

registry.register(
    name="list_sub_agents",
    toolset="sub_agent",
    schema=LIST_SCHEMA,
    handler=_handle_list,
)

registry.register(
    name="get_sub_agent_result",
    toolset="sub_agent",
    schema=GET_RESULT_SCHEMA,
    handler=_handle_get_result,
)
