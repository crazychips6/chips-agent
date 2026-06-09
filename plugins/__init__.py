"""chips 插件系统

包含 Tool/Hook/Skill 三类插件支持。
"""

from plugins.manager import PluginManager
from plugins.protocol import HookPlugin

__all__ = [
    "PluginManager",
    "HookPlugin",
]
