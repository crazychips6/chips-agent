"""Skill tools — skills_list / skill_view / skill_manage

三个工具实现渐进式技能加载：
1. ``skills_list`` — 列出可用技能
2. ``skill_view`` — 加载技能完整内容
3. ``skill_manage`` — 管理技能（创建/编辑/删除）
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.skill import SkillManager
    from plugins.manager import PluginManager

from tool.registry import registry

logger = logging.getLogger("chips.tool.builtins.skill_tools")

# 模块级单例，由首次调用时通过 wiring 注入
_skill_manager: SkillManager | None = None  # type: ignore[assignment]
_plugin_manager: PluginManager | None = None  # type: ignore[assignment]


def wire_skill_manager(mgr: SkillManager) -> None:
    """注入 SkillManager 实例。在 cli.py 启动时调用。"""
    global _skill_manager
    _skill_manager = mgr


def wire_plugin_manager(pm: PluginManager) -> None:
    """注入 PluginManager 实例，用于解析插件技能（``plugin:name``）。"""
    global _plugin_manager
    _plugin_manager = pm


# ── 工具处理器 ──


def _handle_skills_list(args: dict[str, Any]) -> str:
    """列出所有可用技能（含插件技能）。"""
    mgr = _skill_manager
    pm = _plugin_manager
    lines = []
    count = 0

    # 文件系统技能
    if mgr is not None:
        skills = mgr.list_skills()
        if skills:
            lines.append(f"可用技能 ({len(skills)} 个):")
            for s in skills:
                lines.append(f"  - {s.name}: {s.description}")
            count += len(skills)

    # 插件技能（qualified name 需用 skill_view("plugin:name") 加载）
    if pm is not None:
        plugin_sks = pm.list_plugin_skills()
        if plugin_sks:
            if lines:
                lines.append("")
            lines.append(f"插件技能 ({len(plugin_sks)} 个，用 skill_view(\"plugin:name\") 加载):")
            for s in plugin_sks:
                qn = s.get("qualified_name", f"{s['plugin_name']}:{s['name']}")
                lines.append(f"  - {qn}: {s.get('description', '')}")
            count += len(plugin_sks)

    if count == 0:
        return "当前没有可用技能。"
    return "\n".join(lines)


def _read_skill_content(path: str) -> str:
    """读取 SKILL.md 文件，去掉 frontmatter 返回正文。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return json.dumps({"error": "无法读取技能文件"})
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].strip()
    return text.strip()


def _handle_skill_view(args: dict[str, Any]) -> str:
    """查看技能完整内容。``plugin:name`` 解析为插件技能。"""
    name = args.get("name", "")
    if not name:
        return json.dumps({"error": "缺少 name 参数"})

    # 插件技能路由：qualified name 含 ":"，不在文件系统索引中
    if ":" in name:
        pm = _plugin_manager
        if pm is None:
            return json.dumps({"error": "Plugin system not initialized"})
        info = pm.find_plugin_skill(name)
        if info is None:
            return json.dumps({"error": f"插件技能 '{name}' 不存在"})
        skill_path = info.get("path")
        if not skill_path or not os.path.isfile(skill_path):
            return json.dumps({"error": f"插件技能 '{name}' 文件不可读"})
        return _read_skill_content(skill_path)

    # 文件系统技能
    mgr = _skill_manager
    if mgr is None:
        return json.dumps({"error": "Skill system not initialized"})
    content = mgr.view_skill_content(name)
    if content is None:
        return json.dumps({"error": f"技能 '{name}' 不存在"})
    return content


def _handle_skill_manage(args: dict[str, Any]) -> str:
    """管理技能（创建/编辑/删除）。"""
    mgr = _skill_manager
    if mgr is None:
        return json.dumps({"error": "Skill system not initialized"})
    action = args.get("action", "")
    name = args.get("name", "")

    if action == "create":
        desc = args.get("description", "")
        content = args.get("content", "")
        if not name or not content:
            return json.dumps({"error": "create 需要 name, content 参数"})
        fpath = mgr.create_skill(name, desc, content)
        if fpath is None:
            return json.dumps({"error": f"创建技能 '{name}' 失败"})
        return f"已创建技能 '{name}'"

    elif action == "delete":
        if not name:
            return json.dumps({"error": "delete 需要 name 参数"})
        ok = mgr.delete_skill(name)
        if not ok:
            return json.dumps({"error": f"删除技能 '{name}' 失败"})
        return f"已删除技能 '{name}'"

    else:
        return json.dumps({"error": f"未知操作: {action}（支持: create, delete）"})


# ── 注册 ──

_SKILL_TOOLS = [
    {
        "name": "skills_list",
        "schema": {
            "type": "function",
            "function": {
                "name": "skills_list",
                "description": "列出所有可用技能的 name 和 description（不含完整内容）。按 skill 名称排序。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "handler": _handle_skills_list,
    },
    {
        "name": "skill_view",
        "schema": {
            "type": "function",
            "function": {
                "name": "skill_view",
                "description": "加载指定技能的完整 markdown 内容。SKILL.md 包含流程说明。调用前先用 skills_list 确认技能存在。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "技能名"},
                    },
                    "required": ["name"],
                },
            },
        },
        "handler": _handle_skill_view,
    },
    {
        "name": "skill_manage",
        "schema": {
            "type": "function",
            "function": {
                "name": "skill_manage",
                "description": "管理技能：创建新技能（create）或删除已有技能（delete）。编辑请先 view → 修改 → delete + create。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "delete"],
                            "description": "操作类型",
                        },
                        "name": {"type": "string", "description": "技能名"},
                        "description": {"type": "string", "description": "技能描述（仅在 create 时需要）"},
                        "content": {"type": "string", "description": "技能完整 markdown 正文（仅在 create 时需要）"},
                    },
                    "required": ["action", "name"],
                },
            },
        },
        "handler": _handle_skill_manage,
    },
]


def _register():
    for t in _SKILL_TOOLS:
        registry.register(
            name=t["name"],
            toolset="skills",
            schema=t["schema"],
            handler=t["handler"],
        )


_register()
