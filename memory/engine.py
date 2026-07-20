"""ActiveMemoryEngine — 主动记忆引擎

基于 Oracle Agent Memory 架构设计：
- Ingestion: 摄取内容，提取记忆
- Extraction: 从内容中提取结构化记忆
- Consolidation: 合并重复信息
- Retrieval: scope 感知检索
- Revision: 检测并修订过时记忆
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from memory.models import (
    MemoryEntry, MemoryScope, MemoryStatus, MemoryType, 
    ExtractedMemory, generate_memory_id
)
from memory.storage import StorageAdapter

logger = logging.getLogger("chips.memory.engine")


class MemoryExtractor:
    """记忆提取器 — 从内容中提取结构化记忆。"""
    
    # 偏好模式
    _PREFERENCE_PATTERNS = [
        (r"喜欢|偏好|习惯|常用", MemoryType.USER_PREFERENCE, 0.8),
        (r"不喜欢|不想|不要|别", MemoryType.USER_PREFERENCE, 0.7),
        (r"请用|使用|用.*代替", MemoryType.USER_PREFERENCE, 0.8),
    ]
    
    # 事实模式
    _FACT_PATTERNS = [
        (r"是|叫做|名为|位于", MemoryType.USER_FACT, 0.7),
        (r"版本|版本号|v\d+", MemoryType.ENVIRONMENT, 0.8),
        (r"安装|配置|设置", MemoryType.ENVIRONMENT, 0.7),
    ]
    
    def extract(self, content: str, source: str = "conversation") -> list[ExtractedMemory]:
        """从内容中提取记忆。"""
        results = []
        
        # 规则提取
        for pattern, mem_type, confidence in self._PREFERENCE_PATTERNS + self._FACT_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                results.append(ExtractedMemory(
                    content=content[:200],
                    memory_type=mem_type,
                    confidence=confidence,
                    importance=0.6 if mem_type == MemoryType.USER_PREFERENCE else 0.5,
                ))
                return results
        
        # 如果没有匹配，作为通用事实（降低阈值到 5 个字符）
        if len(content) >= 2:
            results.append(ExtractedMemory(
                content=content[:200],
                memory_type=MemoryType.USER_FACT,
                confidence=0.5,
                importance=0.4,
            ))
        
        return results


class MemoryConsolidator:
    """记忆整合器 — 合并重复信息，更新置信度。"""
    
    def __init__(self, storage: StorageAdapter):
        self._storage = storage
    
    def consolidate(self, scope: MemoryScope) -> int:
        """整合 scope 内的记忆，返回合并数量。"""
        entries = self._storage.query(scope, limit=1000)
        
        # 检测重复
        duplicates = self._find_duplicates(entries)
        
        # 合并重复
        merged_count = 0
        for group in duplicates:
            merged = self._merge_group(group)
            if merged:
                self._storage.update(merged)
                
                # 归档被合并的条目
                for entry in group:
                    if entry.id != merged.id:
                        entry.status = MemoryStatus.ARCHIVED
                        entry.parent_id = merged.id
                        self._storage.update(entry)
                        merged_count += 1
        
        return merged_count
    
    def _find_duplicates(self, entries: list[MemoryEntry]) -> list[list[MemoryEntry]]:
        """检测重复条目。"""
        groups = []
        processed: set[str] = set()
        
        for i, e1 in enumerate(entries):
            if e1.id in processed:
                continue
            
            group = [e1]
            for e2 in entries[i+1:]:
                if e2.id in processed:
                    continue
                if self._is_similar(e1, e2):
                    group.append(e2)
                    processed.add(e2.id)
            
            if len(group) > 1:
                groups.append(group)
                processed.add(e1.id)
        
        return groups
    
    def _is_similar(self, e1: MemoryEntry, e2: MemoryEntry) -> bool:
        """判断两个条目是否相似。"""
        if e1.memory_type != e2.memory_type:
            return False
        
        # 简单的字符串相似度检查
        if e1.content == e2.content:
            return True
        
        # 检查包含关系
        if e1.content in e2.content or e2.content in e1.content:
            return True
        
        return False
    
    def _merge_group(self, group: list[MemoryEntry]) -> MemoryEntry | None:
        """合并一组重复条目。"""
        if not group:
            return None
        
        # 选择置信度最高的作为主条目
        best = max(group, key=lambda e: e.confidence)
        
        # 合并内容（保留最长的）
        for entry in group:
            if len(entry.content) > len(best.content):
                best.content = entry.content
        
        # 更新置信度（取平均）
        best.confidence = sum(e.confidence for e in group) / len(group)
        
        # 合并标签
        all_tags = set()
        for entry in group:
            all_tags.update(entry.tags)
        best.tags = list(all_tags)
        
        best.updated_at = datetime.now()
        best.version += 1
        
        return best


class MemoryReviser:
    """记忆修订器 — 检测并修订过时记忆。"""
    
    def __init__(self, storage: StorageAdapter):
        self._storage = storage
    
    def revise(self, scope: MemoryScope) -> int:
        """修订过时记忆，返回修订数量。"""
        entries = self._storage.query(scope, limit=1000)
        revised_count = 0
        
        for entry in entries:
            if entry.is_stale():
                # 标记需要审核
                entry.status = MemoryStatus.NEEDS_REVIEW
                self._storage.update(entry)
                revised_count += 1
        
        return revised_count


class ActiveMemoryEngine:
    """主动记忆引擎 — 管理记忆生命周期。"""
    
    def __init__(self, storage: StorageAdapter):
        self._storage = storage
        self._extractor = MemoryExtractor()
        self._consolidator = MemoryConsolidator(storage)
        self._reviser = MemoryReviser(storage)
    
    @property
    def storage(self) -> StorageAdapter:
        return self._storage
    
    # ── Ingestion (摄取) ──
    
    def ingest(self, content: str, scope: MemoryScope, 
               source: str = "conversation") -> list[MemoryEntry]:
        """摄取内容，提取并存储记忆。"""
        # 1. 提取关键信息
        extracted = self._extractor.extract(content, source)
        
        # 2. 保存到存储
        entries = []
        for item in extracted:
            entry = MemoryEntry(
                id=generate_memory_id(),
                content=item.content,
                memory_type=item.memory_type,
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                thread_id=scope.thread_id,
                source=source,
                confidence=item.confidence,
                importance=item.importance,
                tags=item.tags,
            )
            self._storage.save(entry)
            entries.append(entry)
        
        # 3. 触发 consolidation（每 10 次摄取触发一次）
        if len(entries) > 0:
            self._consolidator.consolidate(scope)
        
        return entries
    
    # ── Retrieval (检索) ──
    
    def retrieve(self, query: str, scope: MemoryScope,
                 limit: int = 5) -> list[MemoryEntry]:
        """检索相关记忆。"""
        # 1. scope 感知查询
        candidates = self._storage.query(scope, limit=limit * 2)
        
        # 2. 简单相关性排序（基于关键词匹配）
        scored = []
        query_lower = query.lower()
        for entry in candidates:
            score = 0.0
            content_lower = entry.content.lower()
            
            # 关键词匹配
            for word in query_lower.split():
                if word in content_lower:
                    score += 0.3
            
            # 重要性加成
            score += entry.importance * 0.3
            
            # 置信度加成
            score += entry.confidence * 0.2
            
            # 最近访问加成
            if entry.accessed_at:
                days_since = (datetime.now() - entry.accessed_at).days
                if days_since < 7:
                    score += 0.2
            
            scored.append((score, entry))
        
        # 3. 返回 top-k
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]
    
    # ── Revision (修订) ──
    
    def revise(self, scope: MemoryScope) -> int:
        """修订过时记忆，返回修订数量。"""
        return self._reviser.revise(scope)
    
    # ── 统计 ──
    
    def count(self, scope: MemoryScope) -> int:
        """统计 scope 内的记忆条目数量。"""
        return self._storage.count(scope)
