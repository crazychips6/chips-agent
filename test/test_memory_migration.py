"""Memory Migration 测试"""

import os
import tempfile
import pytest

from memory.migration import migrate_memory_files, backup_memory_files, read_entries
from memory.models import MemoryScope, MemoryType
from memory.storage import SQLiteStorageAdapter


class TestMigration:
    """迁移测试。"""

    def test_read_entries(self):
        """测试：读取旧格式条目。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            path = os.path.join(tmpdir, "TEST.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("条目1\n§\n条目2\n§\n条目3")
            
            entries = read_entries(path)
            assert len(entries) == 3
            assert entries[0] == "条目1"

    def test_migrate_memory_files(self):
        """测试：迁移记忆文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建旧格式文件
            memory_dir = tmpdir
            with open(os.path.join(memory_dir, "MEMORY.md"), "w", encoding="utf-8") as f:
                f.write("项目使用 Python\n§\n使用 uv 管理依赖")
            
            with open(os.path.join(memory_dir, "USER.md"), "w", encoding="utf-8") as f:
                f.write("用户偏好深色主题")
            
            db_path = os.path.join(tmpdir, "test.db")
            
            # 迁移
            migrated = migrate_memory_files(memory_dir, "test_user", db_path)
            assert migrated == 3
            
            # 验证
            storage = SQLiteStorageAdapter(db_path)
            scope = MemoryScope(user_id="test_user")
            entries = storage.query(scope, limit=10)
            assert len(entries) == 3

    def test_backup_memory_files(self):
        """测试：备份记忆文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            with open(os.path.join(tmpdir, "MEMORY.md"), "w", encoding="utf-8") as f:
                f.write("测试内容")
            
            # 备份
            backup_dir = backup_memory_files(tmpdir)
            assert backup_dir is not None
            assert os.path.exists(os.path.join(backup_dir, "MEMORY.md"))

    def test_migrate_episodic_with_timestamp(self):
        """测试：迁移带时间戳的 episodic 条目。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建带时间戳的 episodic 文件
            with open(os.path.join(tmpdir, "EPISODIC.md"), "w", encoding="utf-8") as f:
                f.write("[2026-01-15 10:30] 用户询问了天气\n§\n[2026-01-16 14:20] 用户请求翻译文档")
            
            db_path = os.path.join(tmpdir, "test.db")
            
            # 迁移
            migrated = migrate_memory_files(tmpdir, "test_user", db_path)
            assert migrated == 2
            
            # 验证时间戳
            storage = SQLiteStorageAdapter(db_path)
            scope = MemoryScope(user_id="test_user")
            entries = storage.query(scope, limit=10)
            
            assert len(entries) == 2
            # 检查时间戳是否被正确解析
            assert entries[0].created_at.year == 2026
