"""BuiltinMemoryProvider — 内置文件级记忆提供者

封装 MemoryStore，提供 system_prompt_block() + memory 工具。
始终在 MemoryManager 中注册，不可移除。"""

from __future__ import annotations

import json
from typing import Any

from memory.provider import MemoryProvider
from memory.store import MemoryStore, _scan_injection

# memory 工具的 OpenAI function-calling schema
MEMORY_SCHEMA: dict = {
    "name": "memory",
    "description": (
        "保存/管理持久化记忆。每次写入都返回当前全部条目，无需额外读取。\n\n"
        "适用场景：用户说「记住这个」「以后按这个来」「别忘了」；"
        "发现关于环境/项目/工具的事实；用户表达了偏好或习惯。\n\n"
        "不要保存：临时任务状态、会话进度、已完成的日志。\n\n"
        "分类：\n"
        "- memory: 你的个人笔记（环境、项目、工具知识）\n"
        "- user: 关于用户的信息（偏好、角色、习惯）\n"
        "- episodic: 会话摘要（自动带时间戳，仅支持 add）"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "add=追加, replace=替换, remove=删除",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "episodic"],
                "description": "memory=笔记, user=用户档案, episodic=会话摘要",
            },
            "content": {
                "type": "string",
                "description": "条目内容（add/replace 必填）",
            },
            "old_text": {
                "type": "string",
                "description": "要替换/删除的条目标识子串（replace/remove 必填）",
            },
        },
        "required": ["action"],
    },
}


class BuiltinMemoryProvider(MemoryProvider):
    def __init__(self, memory_dir: str = ".memory",
                 memory_char_limit: int = 2200,
                 user_char_limit: int = 1375):
        self._store = MemoryStore(
            memory_dir=memory_dir,
            memory_char_limit=memory_char_limit,
            user_char_limit=user_char_limit,
        )

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True

    def system_prompt_block(self) -> str:
        return self._store.for_system_prompt()

    def get_tool_schemas(self) -> list[dict]:
        return [MEMORY_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        if tool_name != "memory":
            return json.dumps({"error": f"builtin 不处理工具 {tool_name}"})

        action = args.get("action", "")
        target = args.get("target", "memory")
        content = args.get("content", "")
        old_text = args.get("old_text", "")

        if action == "add":
            result = self._store.add(target, content)
        elif action == "replace":
            result = self._store.replace(target, old_text, content)
        elif action == "remove":
            result = self._store.remove(target, old_text)
        else:
            result = {"success": False, "error": f"未知操作: {action}"}

        return json.dumps(result, ensure_ascii=False)

    # ── 生命周期 ──

    def shutdown(self) -> None:
        self._store = None  # type: ignore[assignment]
