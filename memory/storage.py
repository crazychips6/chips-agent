"""StorageAdapter — 存储适配器抽象基类

基于 Oracle Agent Memory 架构设计：
- StorageAdapter: 抽象基类，定义存储接口
- SQLiteStorageAdapter: SQLite 实现
"""

from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from memory.models import MemoryEntry, MemoryScope, MemoryStatus, MemoryType

logger = logging.getLogger("chips.memory.storage")


class StorageAdapter(ABC):
    """存储适配器抽象基类。"""
    
    @abstractmethod
    def save(self, entry: MemoryEntry) -> bool:
        """保存记忆条目。"""
    
    @abstractmethod
    def get(self, entry_id: str) -> MemoryEntry | None:
        """获取记忆条目。"""
    
    @abstractmethod
    def query(self, scope: MemoryScope, 
              memory_type: MemoryType | None = None,
              tags: list[str] | None = None,
              limit: int = 10) -> list[MemoryEntry]:
        """查询记忆条目。"""
    
    @abstractmethod
    def update(self, entry: MemoryEntry) -> bool:
        """更新记忆条目。"""
    
    @abstractmethod
    def delete(self, entry_id: str) -> bool:
        """删除记忆条目。"""
    
    @abstractmethod
    def count(self, scope: MemoryScope) -> int:
        """统计 scope 内的记忆条目数量。"""


class SQLiteStorageAdapter(StorageAdapter):
    """SQLite 存储适配器。"""
    
    def __init__(self, db_path: str = ".chips/memory.db"):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表。"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    user_id TEXT,
                    agent_id TEXT,
                    thread_id TEXT,
                    source TEXT,
                    confidence REAL DEFAULT 1.0,
                    importance REAL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    accessed_at TEXT,
                    access_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    version INTEGER DEFAULT 1,
                    parent_id TEXT,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_user_id ON memory_entries(user_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_agent_id ON memory_entries(agent_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_entries(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(memory_type)
            """)
            conn.commit()
    
    def save(self, entry: MemoryEntry) -> bool:
        """保存记忆条目。"""
        try:
            data = entry.to_dict()
            data["tags"] = json.dumps(data["tags"])
            data["metadata"] = json.dumps(data["metadata"])
            
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    INSERT INTO memory_entries 
                    (id, content, memory_type, user_id, agent_id, thread_id,
                     source, confidence, importance, created_at, updated_at,
                     accessed_at, access_count, status, version, parent_id, tags, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data["id"], data["content"], data["memory_type"],
                    data["user_id"], data["agent_id"], data["thread_id"],
                    data["source"], data["confidence"], data["importance"],
                    data["created_at"], data["updated_at"], data["accessed_at"],
                    data["access_count"], data["status"], data["version"],
                    data["parent_id"], data["tags"], data["metadata"]
                ))
                conn.commit()
            return True
        except Exception as e:
            logger.error("save failed: %s", e)
            return False
    
    def get(self, entry_id: str) -> MemoryEntry | None:
        """获取记忆条目（排除已删除）。"""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM memory_entries WHERE id = ? AND status != 'deleted'", (entry_id,)
                ).fetchone()
                if row:
                    return self._row_to_entry(row)
        except Exception as e:
            logger.error("get failed: %s", e)
        return None
    
    def query(self, scope: MemoryScope, 
              memory_type: MemoryType | None = None,
              tags: list[str] | None = None,
              limit: int = 10) -> list[MemoryEntry]:
        """查询记忆条目。"""
        try:
            conditions = ["status = 'active'"]
            params: list[Any] = []
            
            if scope.user_id:
                conditions.append("user_id = ?")
                params.append(scope.user_id)
            
            if scope.agent_id:
                conditions.append("agent_id = ?")
                params.append(scope.agent_id)
            
            if memory_type:
                conditions.append("memory_type = ?")
                params.append(memory_type.value)
            
            where_clause = " AND ".join(conditions)
            
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"SELECT * FROM memory_entries WHERE {where_clause} LIMIT ?",
                    params + [limit]
                ).fetchall()
                return [self._row_to_entry(row) for row in rows]
        except Exception as e:
            logger.error("query failed: %s", e)
            return []
    
    def update(self, entry: MemoryEntry) -> bool:
        """更新记忆条目。"""
        try:
            data = entry.to_dict()
            data["tags"] = json.dumps(data["tags"])
            data["metadata"] = json.dumps(data["metadata"])
            
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    UPDATE memory_entries SET
                    content = ?, memory_type = ?, user_id = ?, agent_id = ?,
                    thread_id = ?, source = ?, confidence = ?, importance = ?,
                    created_at = ?, updated_at = ?, accessed_at = ?,
                    access_count = ?, status = ?, version = ?, parent_id = ?,
                    tags = ?, metadata = ?
                    WHERE id = ?
                """, (
                    data["content"], data["memory_type"], data["user_id"],
                    data["agent_id"], data["thread_id"], data["source"],
                    data["confidence"], data["importance"], data["created_at"],
                    data["updated_at"], data["accessed_at"], data["access_count"],
                    data["status"], data["version"], data["parent_id"],
                    data["tags"], data["metadata"], data["id"]
                ))
                conn.commit()
            return True
        except Exception as e:
            logger.error("update failed: %s", e)
            return False
    
    def delete(self, entry_id: str) -> bool:
        """删除记忆条目（软删除）。"""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "UPDATE memory_entries SET status = 'deleted' WHERE id = ?",
                    (entry_id,)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error("delete failed: %s", e)
            return False
    
    def count(self, scope: MemoryScope) -> int:
        """统计 scope 内的记忆条目数量。"""
        try:
            conditions = ["status = 'active'"]
            params: list[Any] = []
            
            if scope.user_id:
                conditions.append("user_id = ?")
                params.append(scope.user_id)
            
            if scope.agent_id:
                conditions.append("agent_id = ?")
                params.append(scope.agent_id)
            
            where_clause = " AND ".join(conditions)
            
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM memory_entries WHERE {where_clause}",
                    params
                ).fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error("count failed: %s", e)
            return 0
    
    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        """将数据库行转换为 MemoryEntry。"""
        return MemoryEntry.from_dict({
            "id": row["id"],
            "content": row["content"],
            "memory_type": row["memory_type"],
            "user_id": row["user_id"] or "",
            "agent_id": row["agent_id"] or "",
            "thread_id": row["thread_id"] or "",
            "source": row["source"] or "",
            "confidence": row["confidence"],
            "importance": row["importance"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "accessed_at": row["accessed_at"],
            "access_count": row["access_count"],
            "status": row["status"],
            "version": row["version"],
            "parent_id": row["parent_id"] or "",
            "tags": json.loads(row["tags"]),
            "metadata": json.loads(row["metadata"]),
        })
