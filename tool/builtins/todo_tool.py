"""todo 工具 — 会话内任务管理

LLM 通过此工具分解复杂任务、跟踪进度。
状态保存在 AIAgent.todo_store 中，每轮对话可读写。

设计：
  - 单个 todo 工具：传入 todos 参数写入，省略则读取
  - 每次调用返回完整列表 + 摘要统计
  - merge=True 按 id 更新现有条目，False 则替换整个列表
"""

from __future__ import annotations

import json
from typing import Any

from tool.registry import registry

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


class TodoStore:
    """会话内任务列表。每个 AIAgent 一个实例。"""

    def __init__(self):
        self._items: list[dict[str, str]] = []

    def write(self, todos: list[dict[str, Any]], merge: bool = False) -> list[dict[str, str]]:
        if not merge:
            self._items = [self._validate(t) for t in self._dedupe_by_id(todos)]
        else:
            existing = {item["id"]: item for item in self._items}
            for t in self._dedupe_by_id(todos):
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue
                if item_id in existing:
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = str(t["content"]).strip()
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                else:
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            seen = set()
            rebuilt = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt
        return self.read()

    def read(self) -> list[dict[str, str]]:
        return [item.copy() for item in self._items]

    def has_items(self) -> bool:
        return bool(self._items)

    @staticmethod
    def _validate(item: dict[str, Any]) -> dict[str, str]:
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            item_id = "?"
        content = str(item.get("content", "")).strip()
        if not content:
            content = "(no description)"
        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"
        return {"id": item_id, "content": content, "status": status}

    @staticmethod
    def _dedupe_by_id(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last_index: dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]


# ── 模块级 store 引用，由 cli.py 通过 wire_store() 注入 ──

_store: TodoStore | None = None


def wire_store(store: TodoStore) -> None:
    global _store
    _store = store


# ── Handler ──


def _handle(args: dict[str, Any]) -> str:
    store = _store
    if store is None:
        return json.dumps({"error": "TodoStore not initialized"})

    todos = args.get("todos")
    merge = args.get("merge", False)

    if todos is not None:
        items = store.write(todos, merge)
    else:
        items = store.read()

    pending = sum(1 for i in items if i["status"] == "pending")
    in_progress = sum(1 for i in items if i["status"] == "in_progress")
    completed = sum(1 for i in items if i["status"] == "completed")
    cancelled = sum(1 for i in items if i["status"] == "cancelled")

    return json.dumps({
        "todos": items,
        "summary": {
            "total": len(items),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "cancelled": cancelled,
        },
    }, ensure_ascii=False)


# ── Schema ──

TODO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "todo",
        "description": "管理会话任务列表（写入/更新/读取）",
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "要写入的条目",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "唯一标识"},
                            "content": {"type": "string", "description": "任务描述"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "cancelled"],
                                "description": "当前状态",
                            },
                        },
                        "required": ["id", "content", "status"],
                    },
                },
                "merge": {
                    "type": "boolean",
                    "description": "按 id 合并（false=替换）",
                    "default": False,
                },
            },
        },
    },
}


# ── Register ──

registry.register(
    name="todo",
    toolset="todo",
    schema=TODO_SCHEMA,
    handler=_handle,
)
