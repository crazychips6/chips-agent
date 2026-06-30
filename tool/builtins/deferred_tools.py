"""tool_describe / tool_request — 延迟工具查询与激活

LLM 通过 <available-deferred-tools> 看到延迟工具列表后，
用这两个工具查询详情或激活使用。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.loop import AIAgent

from tool.registry import registry

logger = logging.getLogger("chips.tool.deferred_tools")

_agent: AIAgent | None = None


def wire_agent(agent: AIAgent) -> None:
    global _agent
    _agent = agent


# ── tool_describe — 查看延迟工具的完整 schema ──


def _handle_describe(args: dict[str, Any]) -> str:
    name = args.get("name", "")
    if not name:
        return json.dumps({"error": "name 不能为空"})

    schema = registry.get_schema(name)
    if schema is None:
        return json.dumps({"error": f"工具 '{name}' 不存在"})

    return json.dumps(schema, ensure_ascii=False)


# ── tool_request — 激活延迟工具到当前会话 ──


def _handle_request(args: dict[str, Any]) -> str:
    agent = _agent
    if agent is None:
        return json.dumps({"error": "agent not initialized"})

    name = args.get("name", "")
    if not name:
        return json.dumps({"error": "name 不能为空"})

    ok = agent.activate_deferred_tool(name)
    if not ok:
        return json.dumps({"error": f"无法激活工具 '{name}'"})

    return json.dumps({
        "status": "activated",
        "tool": name,
        "message": f"工具 '{name}' 已激活，当前会话可用。",
    })


# ── 注册 ──

registry.register(
    name="tool_describe",
    toolset="core",
    group="core",
    schema={
        "type": "function",
        "function": {
            "name": "tool_describe",
            "description": "查看指定工具的完整定义和参数说明",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "工具名",
                    },
                },
                "required": ["name"],
            },
        },
    },
    handler=_handle_describe,
)

registry.register(
    name="tool_request",
    toolset="core",
    group="core",
    schema={
        "type": "function",
        "function": {
            "name": "tool_request",
            "description": "激活一个延迟加载的工具到当前会话，激活后可直接使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要激活的工具名",
                    },
                },
                "required": ["name"],
            },
        },
    },
    handler=_handle_request,
)
