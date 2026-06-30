"""工具分组定义 — 控制哪些工具默认加载、哪些延迟加载。

分组规则：
  core  → 永远加载，不进 deferred 列表
  dev   → 默认延迟，可通过 tool_request 激活
  agent → 默认延迟，可通过 tool_request 激活
"""

# 分组定义（组名 → 显示名 + 描述）
TOOL_GROUPS: dict[str, dict[str, str]] = {
    "core": {
        "name": "核心工具",
        "description": "始终可用",
    },
    "dev": {
        "name": "开发工具",
        "description": "进程管理、系统信息、截图、日历",
    },
    "agent": {
        "name": "Agent 工具",
        "description": "技能管理、子 Agent 查询",
    },
}

# 非 core 组的工具默认延迟加载
DEFERRED_GROUPS = {"dev", "agent"}
