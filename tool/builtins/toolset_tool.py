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
        lines = ["当前工具集状态："]
        for ts_name in sorted(agent.enabled_toolsets):
            ok = is_toolset_available(ts_name)
            ts = get_toolset(ts_name)
            desc = ts.get("description", "") if ts else ""
            tools = resolve_toolset(ts_name)
            status = "✓" if ok else "✗"
            lines.append(f"  [{status}] {ts_name} — {desc} ({len(tools)} 个工具)")
        lines.append(f"\n可用工具总数: {len(agent.tool_names)}")
        return "\n".join(lines)

    if action == "enable":
        if not name:
            return json.dumps({"error": "enable 需要 name 参数"})
        if name in agent.enabled_toolsets:
            return f"工具集 '{name}' 已启用，无需重复操作"
        if not get_toolset(name):
            return json.dumps({"error": f"未知工具集: {name}"})
        agent.enabled_toolsets.append(name)
        return f"已启用工具集 '{name}'，后续轮次即可使用"

    if action == "disable":
        if not name:
            return json.dumps({"error": "disable 需要 name 参数"})
        if name == "core":
            return "不能禁用核心工具集（toolset 工具在此工具集中）"
        if name not in agent.enabled_toolsets:
            return f"工具集 '{name}' 未启用，无需禁用"
        agent.enabled_toolsets.remove(name)
        return f"已禁用工具集 '{name}'"

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
