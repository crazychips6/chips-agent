"""/agent 命令 — Agent 角色注册表 CRUD

Agent Registry 的运行时管理接口。
通过 factory function make_handler(agent_registry) 注入依赖，
避免闭包耦合到 cli.py。
"""

from __future__ import annotations

from typing import Any, Callable

from config.agent_config import AgentRegistry


def _sync_agent_schemas(agent_registry: AgentRegistry) -> None:
    """将 agent_registry 中的角色名同步到 delegate_task/orchestrate 的 schema enum。"""
    from tool.registry import registry as _tool_registry

    _agent_names = agent_registry.names
    _de = _tool_registry._entries.get("delegate_task")
    if _de and _agent_names:
        _de.schema["function"]["parameters"]["properties"]["agent"]["enum"] = _agent_names
    _orch = _tool_registry._entries.get("orchestrate")
    if _orch and _agent_names:
        _os = _orch.schema
        _os["function"]["parameters"]["properties"]["steps"]["items"]["properties"]["agent"]["enum"] = _agent_names
        _os["function"]["parameters"]["properties"]["agents"]["items"]["enum"] = _agent_names


def _parse_kwargs(raw: list[str]) -> dict:
    """解析 --key value 对。"""
    kwargs: dict = {}
    i = 0
    while i < len(raw):
        arg = raw[i]
        if arg.startswith("--"):
            key = arg[2:]
            if i + 1 < len(raw) and not raw[i + 1].startswith("--"):
                val = raw[i + 1]
                i += 2
                if key in ("tools",):
                    kwargs[key] = [t.strip() for t in val.split(",") if t.strip()]
                elif key in ("iterations", "max_iterations", "pool", "pool_size"):
                    target = "max_iterations" if key in ("iterations",) else "pool_size" if key in ("pool",) else key
                    try:
                        kwargs[target] = int(val)
                    except ValueError:
                        pass
                elif key in ("model", "prompt", "system_prompt", "description"):
                    kwargs[{"prompt": "system_prompt"}.get(key, key)] = val
            else:
                kwargs[key] = True
                i += 1
        else:
            i += 1
    return kwargs


_USAGE = (
    "/agent list | add <name> [--tools ...] [--model ...] [--iterations N] "
    "| remove <name> | update <name> [--tools ...]"
)


def make_handler(agent_registry: AgentRegistry) -> Callable[[list[str]], str | None]:
    """创建 /agent 命令处理器。

    Args:
        agent_registry: 已初始化的 AgentRegistry 实例

    Returns:
        handler(args) -> str | None，符合 CommandRegistry 签名
    """

    def handler(args: list[str]) -> str | None:
        if not args:
            return f"用法：{_USAGE}"

        sub = args[0]

        if sub == "list":
            agents = agent_registry.list()
            if not agents:
                return "ℹ 暂无已注册 Agent 角色。"
            lines = ["已注册 Agent 角色："]
            for a in agents:
                tools = ", ".join(a.get("tools", []))
                lines.append(f"  {a['name']:20s} model={a.get('model', '?'):20s} tools=[{tools}]")
            return "\n".join(lines)

        if sub == "add":
            if len(args) < 2:
                return f"用法：/agent add <name> [--tools t1,t2] [--model m] [--prompt p] [--iterations N] [--pool N]"
            name = args[1]
            kwargs = _parse_kwargs(args[2:])
            try:
                entry = agent_registry.register(name, kwargs)
                _sync_agent_schemas(agent_registry)
                return f"✅ 已注册 Agent「{name}」（model={entry.get('model', '?')}, tools={entry.get('tools', [])}）"
            except ValueError as e:
                return f"⚠ {e}"

        if sub in ("remove", "rm"):
            if len(args) < 2:
                return "用法：/agent remove <name>"
            name = args[1]
            if agent_registry.unregister(name):
                _sync_agent_schemas(agent_registry)
                return f"✅ 已注销 Agent「{name}」"
            return f"⚠ Agent「{name}」不存在"

        if sub in ("update", "set"):
            if len(args) < 4:
                return "用法：/agent update <name> --tools t1,t2 --model m [--prompt p]"
            name = args[1]
            kwargs = _parse_kwargs(args[2:])
            if not kwargs:
                return "⚠ 请至少指定一个要更新的字段（--tools / --model / --prompt / --iterations / --pool）"
            entry = agent_registry.update(name, kwargs)
            if entry is None:
                return f"⚠ Agent「{name}」不存在"
            _sync_agent_schemas(agent_registry)
            return f"✅ 已更新 Agent「{name}」"

        return f"⚠ 未知子命令：{sub}（支持：list, add, remove, update）"

    return handler
