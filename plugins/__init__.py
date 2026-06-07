"""chips 插件系统

D1 — 插件协议（ToolPlugin / HookPlugin）
D2 — 挂钩点调度（PluginManager）
"""

from plugins.manager import PluginManager
from plugins.protocol import HookPlugin, ToolPlugin

__all__ = [
    "PluginManager",
    "ToolPlugin",
    "HookPlugin",
]
