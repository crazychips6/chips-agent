"""intent_query 工具 — 主 LLM 自救：向端侧小模型查询需要什么工具

当小模型注入的工具不够用时，主 LLM 调用此工具查询小模型获取推荐。
例如：小模型只注入了 bash，但任务需要 web，主 LLM 可问小模型"查天气需要什么工具"
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.loop import AIAgent

from tool.registry import registry

# 由 cli.py 启动时通过 wire_agent() 注入
_agent: AIAgent | None = None


def wire_agent(agent: AIAgent) -> None:
    global _agent
    _agent = agent


def _handle(args: dict[str, Any]) -> str:
    agent = _agent
    if agent is None:
        return json.dumps({"error": "AIAgent not initialized"})
    if agent._local_router is None:
        return "端侧模型路由未加载，尝试使用 toolset enable 激活工具集"

    task = args.get("task", "").strip()
    if not task:
        return "请提供任务描述"

    if not agent._local_router.is_available():
        return "端侧模型暂不可用"

    result = agent._local_router.query_tools(task)
    tools = result.get("tools", [])
    if not tools:
        return f"端侧模型未识别到所需工具: {result.get('reasoning', '')}"

    # 追加到注入工具集，即时生效
    agent._intent_tool_names |= set(tools)
    agent._resolve_tool_names()
    return f"已追加工具: {', '.join(tools)}"


registry.register(
    name="intent_query",
    schema={
        "type": "function",
        "function": {
            "name": "intent_query",
            "description": "向端侧小模型查询当前任务需要的工具，返回推荐工具名列表并自动激活",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "需要分析的任务描述，越具体越好",
                    },
                },
                "required": ["task"],
            },
        },
    },
    handler=_handle,
)
