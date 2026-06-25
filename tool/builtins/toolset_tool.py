"""toolset 工具 — 延迟加载工具集管理

LLM 在对话中可启用/禁用工具集，启用后**当前轮即可使用**，永久有效直到禁用：
  toolset({"action": "enable", "name": "web"})
  toolset({"action": "disable", "name": "web"})
  toolset({"action": "list"})

所有可用 toolset 名在 system prompt 中枚举，LLM 第一轮就知道有哪些可启用。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.loop import AIAgent

from tool.registry import registry
from tool.toolsets import get_toolset, resolve_toolset, is_toolset_available

# 由 cli.py 启动时通过 wire_agent() 注入
_agent: AIAgent | None = None  # type: ignore[assignment]


def wire_agent(agent: AIAgent) -> None:
    global _agent
    _agent = agent


def _handle(args: dict[str, Any]) -> str:
    agent = _agent
    if agent is None:
        return json.dumps({"error": "AIAgent not initialized"})

    action = args.get("action", "list")
    name = args.get("name", "")

    if action == "list":
        from tool.toolsets import CORE_ALWAYS_ON
        lines = ["当前工具状态："]
        lines.append(f"  [★] core — 始终可用（{len(CORE_ALWAYS_ON)} 个核心工具）")
        if agent.permanent_toolsets:
            for ts_name in sorted(agent.permanent_toolsets):
                ts = get_toolset(ts_name)
                desc = ts.get("description", "") if ts else ""
                tools = resolve_toolset(ts_name)
                lines.append(f"  [📌] {ts_name} — {desc}（永久常驻，{len(tools)} 个工具）")
        if agent.active_toolsets:
            for ts_name in sorted(agent.active_toolsets):
                ts = get_toolset(ts_name)
                desc = ts.get("description", "") if ts else ""
                tools = resolve_toolset(ts_name)
                lines.append(f"  [✓] {ts_name} — {desc}（{len(tools)} 个工具）")
        else:
            lines.append("  （无已激活工具集，使用 toolset enable <名> 激活，当前轮即可使用）")
        lines.append(f"\n可用工具总数: {len(agent.tool_names)}")
        return "\n".join(lines)

    if action == "enable":
        if not name:
            return json.dumps({"error": "enable 需要 name 参数"})
        if not get_toolset(name):
            return json.dumps({"error": f"未知工具集: {name}"})
        if name in agent.active_toolsets:
            return f"工具集 '{name}' 已激活，无需重复操作"
        if name in getattr(agent, "permanent_toolsets", []):
            return f"工具集 '{name}' 已是永久常驻，无需激活"
        agent.active_toolsets.add(name)
        # 当前轮立即生效：重新解析 tool_names，下一轮 LLM 调用即可使用
        agent._resolve_tool_names()
        ts = get_toolset(name)
        desc = ts.get("description", "") if ts else ""
        tools = resolve_toolset(name)
        return f"已激活工具集 '{name}'（{desc}，{len(tools)} 个工具），当前轮即可使用"

    if action == "disable":
        if not name:
            return json.dumps({"error": "disable 需要 name 参数"})
        if name not in agent.active_toolsets:
            return f"工具集 '{name}' 未激活"
        agent.active_toolsets.discard(name)
        agent._resolve_tool_names()
        return f"已禁用工具集 '{name}'"

    return json.dumps({"error": f"未知操作: {action}（支持: enable, disable, list）"})


registry.register(
    name="toolset",
    toolset="core",
    schema={
        "type": "function",
        "function": {
            "name": "toolset",
            "description": "管理延迟加载工具集（启用/禁用/查看）",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "enable", "disable"],
                        "description": "list/enable/disable",
                    },
                    "name": {
                        "type": "string",
                        "description": "工具集名（enable/disable）",
                    },
                },
                "required": ["action"],
            },
        },
    },
    handler=_handle,
)
