"""toolset 工具 — 运行时动态开关工具集

LLM 在对话中可自主启用/禁用工具集，无需重启：
  toolset({"action": "enable", "name": "web"})
  toolset({"action": "disable", "name": "web"})
  toolset({"action": "list"})

工具集被禁用后，其下的所有工具在后续轮次中不再出现在 LLM 的 tools 参数中。
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
        if agent.hot_zone:
            for ts_name in sorted(agent.hot_zone):
                tt = agent.hot_zone[ts_name]
                ts = get_toolset(ts_name)
                desc = ts.get("description", "") if ts else ""
                lines.append(f"  [🔥] {ts_name} — {desc}（剩余 {tt} 轮）")
        else:
            lines.append("  （无热区工具，使用 toolset enable 激活）")
        lines.append(f"\n可用工具总数: {len(agent.tool_names)}"
                     f"  |  工具集: toolset list 查看详情  |  启用: toolset enable <名>")
        return "\n".join(lines)

    if action == "enable":
        if not name:
            return json.dumps({"error": "enable 需要 name 参数"})
        if not get_toolset(name):
            return json.dumps({"error": f"未知工具集: {name}"})
        if name in agent.hot_zone:
            return f"工具集 '{name}' 已在热区（剩余 {agent.hot_zone[name]} 轮）"
        if name in getattr(agent, "permanent_toolsets", []):
            return f"工具集 '{name}' 已是永久常驻，无需激活"
        # 加入 hot zone，TTL = 3 轮
        agent.hot_zone[name] = 3
        ts = get_toolset(name)
        desc = ts.get("description", "") if ts else ""
        return f"已激活工具集 '{name}'（{desc}），将在 3 轮无使用后自动退出"

    if action == "disable":
        if not name:
            return json.dumps({"error": "disable 需要 name 参数"})
        if name in agent.hot_zone:
            del agent.hot_zone[name]
            return f"工具集 '{name}' 已从热区移除"
        return f"工具集 '{name}' 不在热区中"

    return json.dumps({"error": f"未知操作: {action}（支持: enable, disable, list）"})


registry.register(
    name="toolset",
    toolset="core",
    schema={
        "type": "function",
        "function": {
            "name": "toolset",
            "description": "管理工具集：启用/禁用/查看。需要某个工具集但当前不可用时，先启用再使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "enable", "disable"],
                        "description": "list=查看状态, enable=启用, disable=禁用",
                    },
                    "name": {
                        "type": "string",
                        "description": "工具集名（enable/disable 时需要）",
                    },
                },
                "required": ["action"],
            },
        },
    },
    handler=_handle,
)
