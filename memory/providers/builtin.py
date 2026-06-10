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
        "Save durable information to persistent memory that survives across sessions. "
        "Memory is injected into future turns, so keep it compact and focused on facts "
        "that will still matter later.\n\n"
        "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
        "- User corrects you or says 'remember this' / 'don't do that again'\n"
        "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
        "- You discover something about the environment (OS, installed tools, project structure)\n"
        "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
        "- You identify a stable fact that will be useful again in future sessions\n\n"
        "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
        "The most valuable memory prevents the user from having to repeat themselves.\n\n"
        "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
        "state to memory; use session_search to recall those from past transcripts.\n"
        "If you've discovered a new way to do something, solved a problem that could be "
        "necessary later, save it as a skill with the skill tool.\n\n"
        "TWO TARGETS:\n"
        "- 'user': who the user is -- name, role, preferences, communication style, pet peeves\n"
        "- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned\n\n"
        "ACTIONS: add (new entry), replace (update existing -- old_text identifies it), "
        "remove (delete -- old_text identifies it).\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for user profile.",
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'.",
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove.",
            },
        },
        "required": ["action", "target"],
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
