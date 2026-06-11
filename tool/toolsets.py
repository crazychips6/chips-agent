"""TOOLSETS — 工具集定义与解析

工具集（toolset）是具有语义名称的一组工具组合，支持：
  - includes 递归组合
  - check_fn 运行时可用性检查
  - 插件/MCP 动态注册的 toolset 自动发现

用法:
    from tool.toolsets import resolve_toolset, is_toolset_available, build_availability_table

    tools = resolve_toolset("core")
    table = build_availability_table()
"""

from __future__ import annotations

from typing import Any


# 始终暴露给 LLM 的核心工具名（不受 hot zone / permanent 影响）
# 这些工具的 schema 每轮都在 tools 参数中，LLM 永远可以调用
CORE_ALWAYS_ON = {"echo", "clarify", "todo", "toolset", "delegate_task", "orchestrate"}

# ── 静态定义 ──

TOOLSET_SCHEMA: dict[str, dict[str, Any]] = {
    "core": {
        "description": "核心工具集（terminal + file，兼容 --toolset core）",
        "includes": ["terminal", "file"],
    },
    "terminal": {"description": "终端命令执行"},
    "file":     {"description": "文件读写与搜索"},
    "web":      {"description": "网页搜索与内容抓取"},
    "vision":   {"description": "屏幕截图"},
    "skills":   {"description": "技能系统管理"},
    "todo":     {"description": "任务规划与进度跟踪"},
    "clarify":  {"description": "向用户追问澄清"},
    "calendar": {"description": "日程与日历管理"},
    "geo":      {"description": "基于 IP 的地理位置查询"},
    "system":   {"description": "系统信息查询（OS、CPU、内存、磁盘）"},
    "process":  {"description": "进程管理（列出/查看/终止）"},
    "all": {
        "description": "全部可用工具",
        "includes": ["core", "web", "vision", "skills", "calendar", "geo", "system", "process"],
    },
}



# ── 查询 ──


def get_toolset(name: str) -> dict[str, Any] | None:
    """获取工具集定义。

    先在静态 TOOLSET_SCHEMA 中查找，找不到则去 registry 查动态注册的 toolset。
    """
    if name in TOOLSET_SCHEMA:
        return TOOLSET_SCHEMA[name]
    try:
        from tool.registry import registry
        tool_names = registry.get_tool_names_for_toolset(name)
        if tool_names:
            return {
                "description": f"动态工具集: {name}",
                "tools": tool_names,
            }
    except Exception:
        pass
    return None


def get_toolset_names() -> list[str]:
    """返回所有可用 toolset 名（静态 + 动态注册）。"""
    names = set(TOOLSET_SCHEMA.keys())
    try:
        from tool.registry import registry
        names.update(registry.get_registered_toolset_names())
    except Exception:
        pass
    return sorted(names)


def get_all_toolsets() -> dict[str, dict[str, Any]]:
    """返回所有 toolset 定义（静态 + 动态注册）。"""
    result = dict(TOOLSET_SCHEMA)
    for name in get_toolset_names():
        if name not in result:
            ts = get_toolset(name)
            if ts:
                result[name] = ts
    return result


def validate_toolset(name: str) -> bool:
    """检查 toolset 名是否有效。"""
    if name in {"all", "*"}:
        return True
    return get_toolset(name) is not None


# ── 解析 ──


def resolve_toolset(
    name: str,
    _visited: set[str] | None = None,
) -> list[str]:
    """递归展开 toolset，返回扁平工具名列表。"""
    if _visited is None:
        _visited = set()

    if name in {"all", "*"}:
        all_tools: list[str] = []
        for ts_name in get_toolset_names():
            if ts_name in ("all", "*"):
                continue
            for t in resolve_toolset(ts_name, _visited.copy()):
                if t not in all_tools:
                    all_tools.append(t)
        return all_tools

    if name in _visited:
        return []
    _visited.add(name)

    ts = get_toolset(name)
    if not ts:
        return []

    # 1. 显式 tools（可选，静态定义）
    tools = list(ts.get("tools", []))
    seen = set(tools)

    # 2. includes 递归
    for inc in ts.get("includes", []):
        for t in resolve_toolset(inc, _visited):
            if t not in seen:
                tools.append(t)
                seen.add(t)

    # 3. 自动发现 registry 中注册为本 toolset 的工具
    #    （叶子工具集无需静态 tools 列表，避免与自注册重复维护）
    try:
        from tool.registry import registry
        for t in registry.get_tool_names_for_toolset(name):
            if t not in seen:
                tools.append(t)
                seen.add(t)
    except Exception:
        pass

    return tools


def resolve_multiple_toolsets(names: list[str]) -> list[str]:
    """合并多个 toolset 的展平结果。"""
    all_tools: list[str] = []
    seen: set[str] = set()
    for n in names:
        for t in resolve_toolset(n):
            if t not in seen:
                all_tools.append(t)
                seen.add(t)
    return all_tools


# ── 可用性 ──


def is_toolset_available(name: str) -> bool:
    """检查一个 toolset 在当前运行时是否可用（委托 registry）。"""
    try:
        from tool.registry import registry
        return registry.check_toolset_availability(name)
    except Exception:
        return False


def build_availability_table() -> str:
    """构建可用性 Markdown 表格（供 system prompt 注入）。"""
    lines = ["| 工具集 | 状态 | 用途 |", "|--------|------|------|"]
    for name in get_toolset_names():
        if name in ("all", "*"):
            continue
        ts = get_toolset(name)
        if not ts:
            continue
        ok = is_toolset_available(name)
        tools = resolve_toolset(name)
        if not tools:
            continue
        status = "✓" if ok else "✗ 需配置"
        desc = ts.get("description", "")
        lines.append(f"| {name} | {status} | {desc} |")
    return "\n".join(lines) if len(lines) > 1 else ""
