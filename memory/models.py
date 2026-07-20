"""Memory Models — 记忆系统数据模型

基于 Oracle Agent Memory 架构设计：
- MemoryEntry: 统一的记忆条目数据模型
- MemoryType: 记忆类型枚举
- MemoryScope: 范围控制（user/agent/thread 三级隔离）
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MemoryType(Enum):
    """记忆类型枚举。"""
    USER_PREFERENCE = "user_preference"    # 用户偏好
    USER_FACT = "user_fact"                # 用户事实
    ENVIRONMENT = "environment"            # 环境信息
    PROCEDURAL = "procedural"             # 程序性知识
    EPISODIC = "episodic"                  # 情景记忆
    EXPERIENCE = "experience"              # 经验知识


class MemoryStatus(Enum):
    """记忆状态枚举。"""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    NEEDS_REVIEW = "needs_review"


def generate_memory_id() -> str:
    """生成唯一记忆 ID。"""
    return str(uuid.uuid4())[:12]


@dataclass
class MemoryEntry:
    """统一的记忆条目数据模型。"""
    id: str = field(default_factory=generate_memory_id)
    content: str = ""
    memory_type: MemoryType = MemoryType.USER_FACT
    
    # Scope 控制
    user_id: str = ""
    agent_id: str = ""
    thread_id: str = ""
    
    # 元数据
    source: str = ""                 # 来源 (conversation/tool/manual)
    confidence: float = 1.0          # 置信度 (0-1)
    importance: float = 0.5          # 重要性 (0-1)
    
    # 生命周期
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    accessed_at: datetime | None = None
    access_count: int = 0
    
    # 状态
    status: MemoryStatus = MemoryStatus.ACTIVE
    version: int = 1
    
    # 关联
    parent_id: str = ""              # 父条目 ID (用于 consolidation)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "thread_id": self.thread_id,
            "source": self.source,
            "confidence": self.confidence,
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "accessed_at": self.accessed_at.isoformat() if self.accessed_at else None,
            "access_count": self.access_count,
            "status": self.status.value,
            "version": self.version,
            "parent_id": self.parent_id,
            "tags": self.tags,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        """从字典反序列化。"""
        return cls(
            id=data.get("id", generate_memory_id()),
            content=data.get("content", ""),
            memory_type=MemoryType(data.get("memory_type", "user_fact")),
            user_id=data.get("user_id", ""),
            agent_id=data.get("agent_id", ""),
            thread_id=data.get("thread_id", ""),
            source=data.get("source", ""),
            confidence=data.get("confidence", 1.0),
            importance=data.get("importance", 0.5),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.now(),
            accessed_at=datetime.fromisoformat(data["accessed_at"]) if data.get("accessed_at") else None,
            access_count=data.get("access_count", 0),
            status=MemoryStatus(data.get("status", "active")),
            version=data.get("version", 1),
            parent_id=data.get("parent_id", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )
    
    def touch(self):
        """更新访问时间和次数。"""
        self.accessed_at = datetime.now()
        self.access_count += 1
    
    def is_stale(self, days_threshold: int = 60) -> bool:
        """判断记忆是否过时。"""
        if not self.accessed_at:
            days_since_update = (datetime.now() - self.updated_at).days
            return days_since_update > days_threshold
        
        days_since_access = (datetime.now() - self.accessed_at).days
        return days_since_access > days_threshold and self.access_count < 3


@dataclass
class MemoryScope:
    """记忆范围控制。"""
    user_id: str
    agent_id: str = ""
    thread_id: str = ""
    
    def matches(self, entry: MemoryEntry) -> bool:
        """检查记忆条目是否匹配此 scope。"""
        if self.user_id and entry.user_id and entry.user_id != self.user_id:
            return False
        if self.agent_id and entry.agent_id and entry.agent_id != self.agent_id:
            return False
        if self.thread_id and entry.thread_id and entry.thread_id != self.thread_id:
            return False
        return True
    
    def is_subset_of(self, other: MemoryScope) -> bool:
        """检查当前 scope 是否是另一个 scope 的子集。
        
        self 是 other 的子集意味着 self 比 other 更严格（或相等）。
        例如：scope(user1, agent1) 是 scope(user1) 的子集。
        
        规则：
        - self 的限制必须被 other 满足
        - other 可以有额外的限制（不影响）
        """
        # user_id 必须匹配
        if self.user_id != other.user_id:
            return False
        # other 有 agent_id 限制时，self 必须匹配
        if other.agent_id and self.agent_id != other.agent_id:
            return False
        # other 有 thread_id 限制时，self 必须匹配
        if other.thread_id and self.thread_id != other.thread_id:
            return False
        return True
    
    def to_dict(self) -> dict[str, str]:
        """序列化为字典。"""
        return {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "thread_id": self.thread_id,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, str]) -> MemoryScope:
        """从字典反序列化。"""
        return cls(
            user_id=data.get("user_id", ""),
            agent_id=data.get("agent_id", ""),
            thread_id=data.get("thread_id", ""),
        )


@dataclass
class ExtractedMemory:
    """从内容中提取的记忆（中间格式）。"""
    content: str
    memory_type: MemoryType
    confidence: float = 0.8
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
