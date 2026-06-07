"""chips 插件系统

D2 — 挂钩点调度（PluginManager）
"""

from plugins.manager import PluginManager
from plugins.protocol import HookPlugin

__all__ = [
    "PluginManager",
    "HookPlugin",
]
