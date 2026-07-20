"""Memory Compatibility Layer — 兼容层

将新的 Oracle 记忆架构适配到现有的 MemoryProvider 接口。
允许渐进式迁移，新旧系统可以共存。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from memory.engine import ActiveMemoryEngine
from memory.models import MemoryEntry, MemoryScope, MemoryType, MemoryStatus
from memory.provider import MemoryProvider
from memory.storage import SQLiteStorageAdapter

logger = logging.getLogger("chips.memory.compat")


class OracleMemoryProvider(MemoryProvider):
    """基于 Oracle 架构的记忆提供者。"""
    
    def __init__(self, user_id: str = "default", agent_id: str = "",
                 db_path: str = ".chips/memory_oracle.db"):
        self._user_id = user_id
        self._agent_id = agent_id
        self._storage = SQLiteStorageAdapter(db_path)
        self._engine = ActiveMemoryEngine(self._storage)
        self._scope = MemoryScope(user_id=user_id, agent_id=agent_id)
    
    @property
    def name(self) -> str:
        return "oracle"
    
    def is_available(self) -> bool:
        return True
    
    def system_prompt_block(self) -> str:
        """生成 system prompt 内容。"""
        entries = self._engine.storage.query(self._scope, limit=20)
        if not entries:
            return ""
        
        lines = ["# Oracle Memory"]
        for entry in entries:
            type_icon = {
                MemoryType.USER_PREFERENCE: "💙",
                MemoryType.USER_FACT: "📝",
                MemoryType.ENVIRONMENT: "🔧",
                MemoryType.PROCEDURAL: "📚",
                MemoryType.EPISODIC: "📖",
                MemoryType.EXPERIENCE: "⭐",
            }.get(entry.memory_type, "•")
            lines.append(f"{type_icon} {entry.content}")
        
        return "\n".join(lines)
    
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """检索相关记忆。"""
        entries = self._engine.retrieve(query, self._scope, limit=5)
        if not entries:
            return ""
        
        lines = []
        for entry in entries:
            entry.touch()
            self._storage.update(entry)
            lines.append(f"- {entry.content}")
        
        return "\n".join(lines)
    
    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "") -> None:
        """同步一轮对话。"""
        # 摄取用户内容
        self._engine.ingest(user_content, self._scope, source="conversation")
        
        # 摄取助手回复（如果有值得记忆的内容）
        if len(assistant_content) > 50:
            self._engine.ingest(assistant_content, self._scope, source="conversation")
    
    def get_tool_schemas(self) -> list[dict]:
        """返回工具 schema。"""
        return [
            {
                "name": "oracle_memory",
                "description": "Oracle 记忆系统 - 管理持久化记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["remember", "recall", "forget", "consolidate"],
                            "description": "操作类型",
                        },
                        "content": {
                            "type": "string",
                            "description": "记忆内容（remember 时使用）",
                        },
                        "query": {
                            "type": "string",
                            "description": "查询关键词（recall 时使用）",
                        },
                        "entry_id": {
                            "type": "string",
                            "description": "记忆条目 ID（forget 时使用）",
                        },
                    },
                    "required": ["action"],
                },
            }
        ]
    
    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        """处理工具调用。"""
        if tool_name != "oracle_memory":
            return json.dumps({"error": f"未知工具: {tool_name}"})
        
        action = args.get("action", "")
        
        if action == "remember":
            content = args.get("content", "")
            if not content:
                return json.dumps({"error": "content 不能为空"})
            
            entries = self._engine.ingest(content, self._scope, source="tool")
            return json.dumps({
                "success": True,
                "message": f"已记住 {len(entries)} 条记忆",
                "entries": [{"id": e.id, "content": e.content} for e in entries],
            })
        
        elif action == "recall":
            query = args.get("query", "")
            if not query:
                return json.dumps({"error": "query 不能为空"})
            
            entries = self._engine.retrieve(query, self._scope, limit=5)
            return json.dumps({
                "success": True,
                "entries": [{"id": e.id, "content": e.content, "type": e.memory_type.value} for e in entries],
            })
        
        elif action == "forget":
            entry_id = args.get("entry_id", "")
            if not entry_id:
                return json.dumps({"error": "entry_id 不能为空"})
            
            success = self._storage.delete(entry_id)
            return json.dumps({
                "success": success,
                "message": "已删除" if success else "未找到",
            })
        
        elif action == "consolidate":
            merged = self._engine._consolidator.consolidate(self._scope)
            return json.dumps({
                "success": True,
                "message": f"已合并 {merged} 条重复记忆",
            })
        
        else:
            return json.dumps({"error": f"未知操作: {action}"})
    
    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: dict | None = None) -> None:
        """内置记忆写入时的通知。"""
        # 映射到 Oracle 记忆类型
        type_map = {
            "user": MemoryType.USER_PREFERENCE,
            "memory": MemoryType.USER_FACT,
        }
        memory_type = type_map.get(target, MemoryType.USER_FACT)
        
        # 创建条目
        entry = MemoryEntry(
            content=content,
            memory_type=memory_type,
            user_id=self._user_id,
            agent_id=self._agent_id,
            source="builtin_mirror",
        )
        self._storage.save(entry)
    
    def consolidate(self) -> int:
        """整合重复记忆。"""
        return self._engine._consolidator.consolidate(self._scope)
    
    def revise(self) -> int:
        """修订过时记忆。"""
        return self._engine.revise(self._scope)
