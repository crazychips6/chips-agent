"""Oracle 记忆架构测试"""

import tempfile
import pytest
from pathlib import Path

from datetime import datetime, timedelta
from memory.models import MemoryEntry, MemoryScope, MemoryType, MemoryStatus
from memory.storage import SQLiteStorageAdapter
from memory.engine import ActiveMemoryEngine, MemoryExtractor, MemoryConsolidator


class TestMemoryEntry:
    """MemoryEntry 数据模型测试。"""

    def test_creation(self):
        """测试：创建 MemoryEntry。"""
        entry = MemoryEntry(
            content="用户偏好 Python 3.12",
            memory_type=MemoryType.USER_PREFERENCE,
            user_id="user1",
        )
        assert entry.content == "用户偏好 Python 3.12"
        assert entry.memory_type == MemoryType.USER_PREFERENCE
        assert entry.user_id == "user1"
        assert entry.status == MemoryStatus.ACTIVE

    def test_serialization(self):
        """测试：序列化和反序列化。"""
        entry = MemoryEntry(
            id="test-123",
            content="测试内容",
            memory_type=MemoryType.USER_FACT,
            user_id="user1",
            tags=["test"],
        )
        data = entry.to_dict()
        restored = MemoryEntry.from_dict(data)
        
        assert restored.id == "test-123"
        assert restored.content == "测试内容"
        assert restored.tags == ["test"]

    def test_touch(self):
        """测试：更新访问时间。"""
        entry = MemoryEntry(content="测试")
        assert entry.access_count == 0
        
        entry.touch()
        assert entry.access_count == 1
        assert entry.accessed_at is not None

    def test_is_stale(self):
        """测试：过时检测。"""
        from datetime import timedelta
        
        entry = MemoryEntry(content="测试")
        entry.updated_at = datetime.now() - timedelta(days=31)
        
        assert entry.is_stale(days_threshold=30) is True
        assert entry.is_stale(days_threshold=60) is False


class TestMemoryScope:
    """MemoryScope 测试。"""

    def test_matches(self):
        """测试：scope 匹配。"""
        entry = MemoryEntry(
            content="测试",
            user_id="user1",
            agent_id="agent1",
        )
        
        scope1 = MemoryScope(user_id="user1", agent_id="agent1")
        scope2 = MemoryScope(user_id="user2")
        scope3 = MemoryScope(user_id="user1", agent_id="agent2")
        
        assert scope1.matches(entry) is True
        assert scope2.matches(entry) is False
        assert scope3.matches(entry) is False

    def test_is_subset_of(self):
        """测试：scope 子集检查。"""
        scope1 = MemoryScope(user_id="user1")
        scope2 = MemoryScope(user_id="user1", agent_id="agent1")
        scope3 = MemoryScope(user_id="user2")
        
        assert scope2.is_subset_of(scope1) is True
        assert scope1.is_subset_of(scope2) is False
        assert scope3.is_subset_of(scope1) is False


class TestSQLiteStorage:
    """SQLite 存储适配器测试。"""

    def test_save_and_get(self):
        """测试：保存和获取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            storage = SQLiteStorageAdapter(db_path)
            
            entry = MemoryEntry(
                id="test-123",
                content="测试内容",
                memory_type=MemoryType.USER_FACT,
                user_id="user1",
            )
            
            assert storage.save(entry) is True
            retrieved = storage.get("test-123")
            
            assert retrieved is not None
            assert retrieved.content == "测试内容"

    def test_query_by_scope(self):
        """测试：按 scope 查询。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            storage = SQLiteStorageAdapter(db_path)
            
            # 保存不同 scope 的条目
            storage.save(MemoryEntry(id="1", content="用户1的记忆", user_id="user1"))
            storage.save(MemoryEntry(id="2", content="用户2的记忆", user_id="user2"))
            storage.save(MemoryEntry(id="3", content="用户1的另一条", user_id="user1"))
            
            # 按 scope 查询
            scope = MemoryScope(user_id="user1")
            results = storage.query(scope)
            
            assert len(results) == 2
            assert all(r.user_id == "user1" for r in results)

    def test_delete_soft(self):
        """测试：软删除。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            storage = SQLiteStorageAdapter(db_path)
            
            entry = MemoryEntry(id="test-123", content="测试")
            storage.save(entry)
            
            assert storage.delete("test-123") is True
            
            # 软删除后查不到
            retrieved = storage.get("test-123")
            assert retrieved is None


class TestMemoryExtractor:
    """MemoryExtractor 测试。"""

    def test_extract_preference(self):
        """测试：提取偏好。"""
        extractor = MemoryExtractor()
        
        results = extractor.extract("我喜欢用 Python")
        assert len(results) > 0
        assert results[0].memory_type == MemoryType.USER_PREFERENCE

    def test_extract_fact(self):
        """测试：提取事实。"""
        extractor = MemoryExtractor()
        
        results = extractor.extract("这是一个重要的事实")
        assert len(results) > 0


class TestActiveMemoryEngine:
    """ActiveMemoryEngine 集成测试。"""

    def test_ingest_and_retrieve(self):
        """测试：摄取和检索。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            storage = SQLiteStorageAdapter(db_path)
            engine = ActiveMemoryEngine(storage)
            
            scope = MemoryScope(user_id="user1")
            
            # 摄取
            entries = engine.ingest("用户喜欢用 Python", scope)
            assert len(entries) > 0
            
            # 检索
            results = engine.retrieve("Python", scope)
            assert len(results) > 0

    def test_consolidation(self):
        """测试：整合重复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            storage = SQLiteStorageAdapter(db_path)
            engine = ActiveMemoryEngine(storage)
            
            scope = MemoryScope(user_id="user1")
            
            # 保存重复条目
            storage.save(MemoryEntry(id="1", content="重复内容", user_id="user1"))
            storage.save(MemoryEntry(id="2", content="重复内容", user_id="user1"))
            
            # 整合
            merged = engine._consolidator.consolidate(scope)
            assert merged > 0

    def test_scope_isolation(self):
        """测试：scope 隔离。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            storage = SQLiteStorageAdapter(db_path)
            engine = ActiveMemoryEngine(storage)
            
            scope1 = MemoryScope(user_id="user1")
            scope2 = MemoryScope(user_id="user2")
            
            # 摄取不同用户的数据
            engine.ingest("用户1的记忆", scope1)
            engine.ingest("用户2的记忆", scope2)
            
            # 检索时应隔离
            results1 = engine.retrieve("记忆", scope1)
            results2 = engine.retrieve("记忆", scope2)
            
            assert all(r.user_id == "user1" for r in results1)
            assert all(r.user_id == "user2" for r in results2)
