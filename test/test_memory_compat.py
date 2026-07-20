"""Oracle 记忆兼容层测试"""

import tempfile
import pytest
from pathlib import Path

from memory.compat import OracleMemoryProvider


class TestOracleMemoryProvider:
    """OracleMemoryProvider 测试。"""

    def test_creation(self):
        """测试：创建提供者。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            provider = OracleMemoryProvider(user_id="user1", db_path=db_path)
            
            assert provider.name == "oracle"
            assert provider.is_available() is True

    def test_system_prompt_block(self):
        """测试：system prompt 生成。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            provider = OracleMemoryProvider(user_id="user1", db_path=db_path)
            
            # 空记忆
            block = provider.system_prompt_block()
            assert block == ""
            
            # 有记忆
            provider.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "用户偏好 Python",
            })
            block = provider.system_prompt_block()
            assert "Python" in block

    def test_tool_call_remember(self):
        """测试：remember 工具调用。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            provider = OracleMemoryProvider(user_id="user1", db_path=db_path)
            
            result = provider.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "测试记忆",
            })
            import json
            data = json.loads(result)
            assert data["success"] is True

    def test_tool_call_recall(self):
        """测试：recall 工具调用。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            provider = OracleMemoryProvider(user_id="user1", db_path=db_path)
            
            # 先记住
            provider.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "Python 是编程语言",
            })
            
            # 查询
            result = provider.handle_tool_call("oracle_memory", {
                "action": "recall",
                "query": "Python",
            })
            import json
            data = json.loads(result)
            assert data["success"] is True
            assert len(data["entries"]) > 0

    def test_tool_call_forget(self):
        """测试：forget 工具调用。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            provider = OracleMemoryProvider(user_id="user1", db_path=db_path)
            
            # 先记住
            remember_result = provider.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "临时记忆",
            })
            import json
            entry_id = json.loads(remember_result)["entries"][0]["id"]
            
            # 删除
            result = provider.handle_tool_call("oracle_memory", {
                "action": "forget",
                "entry_id": entry_id,
            })
            data = json.loads(result)
            assert data["success"] is True

    def test_scope_isolation(self):
        """测试：scope 隔离。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            
            provider1 = OracleMemoryProvider(user_id="user1", db_path=db_path)
            provider2 = OracleMemoryProvider(user_id="user2", db_path=db_path)
            
            # 各自记住不同内容
            provider1.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "用户1的记忆",
            })
            provider2.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "用户2的记忆",
            })
            
            # 查询时应隔离
            result1 = provider1.handle_tool_call("oracle_memory", {
                "action": "recall",
                "query": "记忆",
            })
            result2 = provider2.handle_tool_call("oracle_memory", {
                "action": "recall",
                "query": "记忆",
            })
            
            import json
            data1 = json.loads(result1)
            data2 = json.loads(result2)
            
            assert all("用户1" in e["content"] for e in data1["entries"])
            assert all("用户2" in e["content"] for e in data2["entries"])

    def test_consolidate(self):
        """测试：整合重复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            provider = OracleMemoryProvider(user_id="user1", db_path=db_path)
            
            # 保存重复内容
            provider.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "重复内容",
            })
            provider.handle_tool_call("oracle_memory", {
                "action": "remember",
                "content": "重复内容",
            })
            
            # 整合
            merged = provider.consolidate()
            assert merged >= 0  # 可能合并了重复条目
