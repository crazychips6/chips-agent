"""内置工具测试 — echo + memory + file 读写 handler"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tool.registry import registry as global_registry

# 触发 echo 等内置工具的自注册
import tool.builtins  # noqa: F401


class TestEchoTool:
    """通过 registry 调用 echo 工具。"""

    def test_echo_basic(self):
        result = global_registry.dispatch("echo", {"text": "hello"})
        assert result == "hello"

    def test_echo_empty(self):
        result = global_registry.dispatch("echo", {"text": ""})
        assert result == ""

    def test_echo_missing_key(self):
        result = global_registry.dispatch("echo", {})
        assert result == ""

    def test_echo_unicode(self):
        result = global_registry.dispatch("echo", {"text": "你好世界 🎉"})
        assert result == "你好世界 🎉"

    def test_echo_registered(self):
        """echo 注册在 core 工具集。"""
        entries = global_registry._entries
        assert "echo" in entries
        assert entries["echo"].toolset == "core"


class TestMemoryTool:
    """通过 handler 函数直接测试 memory 读写逻辑。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        import tool.builtins.memory as mem

        self._orig_store = mem._store
        self.mem_module = mem
        yield
        mem._store = self._orig_store

    def test_read_without_store(self):
        self.mem_module._store = None
        result = self.mem_module._read_handler({"category": "memory"})
        assert result == "记忆系统未初始化"

    def test_read_with_store(self):
        store = MagicMock()
        store.for_system_prompt.return_value = "一些记忆内容"
        self.mem_module._store = store

        result = self.mem_module._read_handler({"category": "memory"})
        assert result == "一些记忆内容"

    def test_read_empty_store(self):
        store = MagicMock()
        store.for_system_prompt.return_value = ""
        self.mem_module._store = store

        result = self.mem_module._read_handler({"category": "memory"})
        assert result == "暂无记忆"

    def test_write_without_store(self):
        self.mem_module._store = None
        result = self.mem_module._save_handler({"content": "数据", "category": "memory"})
        assert result == "记忆系统未初始化"

    def test_write_empty_content(self):
        store = MagicMock()
        self.mem_module._store = store
        result = self.mem_module._save_handler({"content": "", "category": "memory"})
        assert result == "内容不能为空"

    def test_write_success(self):
        store = MagicMock()
        store.add.return_value = {"status": "ok", "category": "memory"}
        self.mem_module._store = store

        result = self.mem_module._save_handler({"content": "重要数据", "category": "memory"})
        assert result == "已保存到 memory"
        store.add.assert_called_once_with("重要数据", "memory")

    def test_write_failure(self):
        store = MagicMock()
        store.add.return_value = {"status": "error", "message": "磁盘满"}
        self.mem_module._store = store

        result = self.mem_module._save_handler({"content": "数据", "category": "memory"})
        assert "保存失败" in result
        assert "磁盘满" in result


class TestFileTool:
    """通过 registry 调用 file_read / file_write 工具。"""

    def test_file_read_nonexistent(self):
        result = global_registry.dispatch("file_read", {"path": "/tmp/nonexistent_file_xyz_123"})
        assert "错误" in result

    def test_file_read_sensitive_env(self):
        result = global_registry.dispatch("file_read", {"path": ".env"})
        assert "拒绝访问" in result or "拒绝" in result

    def test_file_read_sensitive_dot_chips(self):
        result = global_registry.dispatch("file_read", {"path": ".chips/sessions.db"})
        assert "拒绝" in result

    def test_file_read_empty_path(self):
        result = global_registry.dispatch("file_read", {"path": ""})
        assert "错误" in result

    def test_file_read_and_write_roundtrip(self, tmp_path):
        p = tmp_path / "test.txt"
        result = global_registry.dispatch("file_write", {"path": str(p), "content": "hello chips"})
        assert "已写入" in result

        result = global_registry.dispatch("file_read", {"path": str(p)})
        assert result == "hello chips"

    def test_file_write_append(self, tmp_path):
        p = tmp_path / "append.txt"
        global_registry.dispatch("file_write", {"path": str(p), "content": "line1\n"})
        global_registry.dispatch("file_write", {"path": str(p), "content": "line2\n", "mode": "append"})
        result = global_registry.dispatch("file_read", {"path": str(p)})
        assert result == "line1\nline2\n"

    def test_file_write_sensitive_path(self):
        result = global_registry.dispatch("file_write", {"path": "/etc/evil.conf", "content": "bad"})
        assert "拒绝" in result

    def test_file_write_sensitive_git(self):
        result = global_registry.dispatch("file_write", {"path": ".git/HEAD", "content": "hack"})
        assert "拒绝" in result

    def test_file_registered(self):
        entries = global_registry._entries
        assert "file_read" in entries
        assert "file_write" in entries
        assert entries["file_read"].toolset == "core"

    def test_symlink_to_env_is_blocked(self, tmp_path):
        """符号链接指向 .env 应被拦截。"""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=key")
        link = tmp_path / "config"
        link.symlink_to(env_file)
        result = global_registry.dispatch("file_read", {"path": str(link)})
        assert "拒绝" in result

    def test_resolve_dot_dot(self):
        """Path.resolve() 正确处理 .. 穿越。"""
        result = global_registry.dispatch("file_read", {"path": "../" * 10 + "tmp"})
        # 穿越到 /tmp → 解析成功，但 /tmp 是目录不是文件
        assert "不是普通文件" in result or "不存在" in result

    def test_non_dot_env_allowed(self):
        """env.txt 不是 .env，不应被误杀。"""
        result = global_registry.dispatch("file_read", {"path": "env.txt"})
        assert "拒绝" not in result

    def test_etc_prefix_no_false_positive(self, tmp_path):
        """/etc 前缀不误伤 /etcetera。"""
        p = tmp_path / "etcetera.txt"
        p.write_text("safe")
        result = global_registry.dispatch("file_write", {"path": str(p), "content": "test"})
        # 不应因为路径包含 "etc" 就被拦截
        assert "拒绝" not in result
