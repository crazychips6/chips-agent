"""memory.store 单元测试"""

import os
import tempfile

import pytest

from memory.store import MemoryStore


@pytest.fixture
def store():
    tmpdir = tempfile.mkdtemp()
    s = MemoryStore(memory_dir=tmpdir)
    yield s


class TestMemoryStore:
    def test_init_empty(self, store):
        assert store.for_system_prompt() == ""

    def test_add_and_read(self, store):
        store.add("记住：小明喜欢吃苹果")
        snapshot = store.for_system_prompt()
        assert "小明喜欢吃苹果" in snapshot

    def test_add_user_category(self, store):
        store.add("用户是开发者", category="user")
        snapshot = store.for_system_prompt()
        assert "用户是开发者" in snapshot
        assert "关于用户" in snapshot

    def test_invalid_category(self, store):
        result = store.add("test", category="invalid")
        assert result["status"] == "error"

    def test_multiple_adds(self, store):
        store.add("记忆1")
        store.add("记忆2")
        snapshot = store.for_system_prompt()
        assert "记忆1" in snapshot
        assert "记忆2" in snapshot

    def test_persistence_across_reload(self, store):
        store.add("持久化数据")
        # 重新加载（模拟重启）
        store2 = MemoryStore(memory_dir=store._memory_file.rsplit("/", 1)[0] if "/" in store._memory_file else ".memory")
        # 实际上直接用 store 的目录重建
        store2 = MemoryStore(memory_dir=os.path.dirname(store._memory_file))
        snapshot = store2.for_system_prompt()
        assert "持久化数据" in snapshot

    def test_atomic_write_integrity(self, store):
        """原子写不应损坏已有数据。"""
        store.add("原子写入测试")
        path = store._memory_file
        content = open(path).read()
        assert "原子写入测试" in content
        # 验证没有临时文件残留
        tmp_files = [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]
        assert len(tmp_files) == 0

    def test_for_system_prompt_format(self, store):
        store.add("项目记忆", category="memory")
        store.add("用户信息", category="user")
        snapshot = store.for_system_prompt()
        assert "## 记忆" in snapshot
        assert "## 关于用户" in snapshot
        # memory 在 user 前面，中间有换行分隔
        assert snapshot.index("## 记忆") < snapshot.index("## 关于用户")

    def test_get_all(self, store):
        """get_all 返回原始快照 dict，含 memory 和 user 分类。"""
        store.add("记忆A", category="memory")
        store.add("用户偏好", category="user")
        data = store.get_all()
        assert data["memory"] == "记忆A"
        assert data["user"] == "用户偏好"

    def test_get_all_empty(self, store):
        """空 store 的 get_all 返回空字符串。"""
        data = store.get_all()
        assert data == {"memory": "", "user": ""}
