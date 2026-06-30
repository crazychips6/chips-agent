"""skill 工具 — 技能查询与管理（合并 skills_list / skill_view / skill_manage）

使用方式：
  skill({"action": "list"})                              — 列出技能
  skill({"action": "view", "name": "xxx"})               — 查看技能内容
  skill({"action": "create", "name": "xxx", "content": "..."}) — 创建技能
  skill({"action": "delete", "name": "xxx"})              — 删除技能
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

_skill_manager: SkillManager | None = None
_plugin_manager: PluginManager | None = None


def wire_skill_manager(mgr: SkillManager) -> None:
    global _skill_manager
    _skill_manager = mgr


def wire_plugin_manager(pm: PluginManager) -> None:
    global _plugin_manager
    _plugin_manager = pm


# ── 处理器 ──


def _read_skill_content(path: str) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return json.dumps({"error": "无法读取技能文件"})
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].strip()
    return text.strip()


def _handle(args: dict[str, Any]) -> str:
    action = args.get("action", "list")

    if action == "list":
        return _handle_list()
    if action == "view":
        return _handle_view(args)
    if action == "create":
        return _handle_create(args)
    if action == "delete":
        return _handle_delete(args)

    return json.dumps({"error": f"未知操作: {action}（支持: list, view, create, delete）"})


def _handle_list() -> str:
    mgr = _skill_manager
    pm = _plugin_manager
    lines = []
    count = 0

    if mgr is not None:
        skills = mgr.list_skills()
        if skills:
            lines.append(f"可用技能 ({len(skills)} 个):")
            for s in skills:
                lines.append(f"  - {s.name}: {s.description}")
            count += len(skills)

    if pm is not None:
        plugin_sks = pm.list_plugin_skills()
        if plugin_sks:
            if lines:
                lines.append("")
            lines.append(f"插件技能 ({len(plugin_sks)} 个，用 skill view 加载):")
            for s in plugin_sks:
                qn = s.get("qualified_name", f"{s['plugin_name']}:{s['name']}")
                lines.append(f"  - {qn}: {s.get('description', '')}")
            count += len(plugin_sks)

    if count == 0:
        return "当前没有可用技能。"
    return "\n".join(lines)


def _handle_view(args: dict) -> str:
    name = args.get("name", "")
    if not name:
        return json.dumps({"error": "缺少 name 参数"})

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

    mgr = _skill_manager
    if mgr is None:
        return json.dumps({"error": "Skill system not initialized"})
    content = mgr.view_skill_content(name)
    if content is None:
        return json.dumps({"error": f"技能 '{name}' 不存在"})
    return content


def _handle_create(args: dict) -> str:
    mgr = _skill_manager
    if mgr is None:
        return json.dumps({"error": "Skill system not initialized"})
    name = args.get("name", "")
    description = args.get("description", "")
    content = args.get("content", "")
    if not name or not content:
        return json.dumps({"error": "create 需要 name, content 参数"})
    fpath = mgr.create_skill(name, description, content)
    if fpath is None:
        return json.dumps({"error": f"创建技能 '{name}' 失败"})
    return f"已创建技能 '{name}'"


def _handle_delete(args: dict) -> str:
    mgr = _skill_manager
    if mgr is None:
        return json.dumps({"error": "Skill system not initialized"})
    name = args.get("name", "")
    if not name:
        return json.dumps({"error": "delete 需要 name 参数"})
    ok = mgr.delete_skill(name)
    if not ok:
        return json.dumps({"error": f"删除技能 '{name}' 失败"})
    return f"已删除技能 '{name}'"


# ── 注册 ──

registry.register(
    name="skill",
    toolset="skills",
    schema={
        "type": "function",
        "function": {
            "name": "skill",
            "description": "技能管理（list=列出, view=查看, create=创建, delete=删除）",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "view", "create", "delete"],
                        "description": "操作类型",
                    },
                    "name": {
                        "type": "string",
                        "description": "技能名（view/create/delete 使用）",
                    },
                    "description": {
                        "type": "string",
                        "description": "技能描述（create 使用）",
                    },
                    "content": {
                        "type": "string",
                        "description": "技能完整 markdown 正文（create 使用）",
                    },
                },
                "required": ["action"],
            },
        },
    },
    handler=_handle,
    group="agent",
)
