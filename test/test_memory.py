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

    def test_persistence_across_reload(self, store):
        store.add("持久化数据")
        store2 = MemoryStore(memory_dir=os.path.dirname(store._memory_file))
        snapshot = store2.for_system_prompt()
        assert "持久化数据" in snapshot

    def test_atomic_write_integrity(self, store):
        store.add("原子写入测试")
        path = store._memory_file
        content = open(path).read()
        assert "原子写入测试" in content
        tmp_files = [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]
        assert len(tmp_files) == 0

    def test_for_system_prompt_format(self, store):
        store.add("项目记忆", category="memory")
        store.add("用户信息", category="user")
        snapshot = store.for_system_prompt()
        assert "## 持久记忆" in snapshot
        assert "## 关于用户" in snapshot

    def test_get_all(self, store):
        store.add("记忆A", category="memory")
        store.add("用户偏好", category="user")
        data = store.get_all()
        assert data["memory"] == "记忆A"
        assert data["user"] == "用户偏好"

    def test_get_all_empty(self, store):
        data = store.get_all()
        assert data == {"memory": "", "user": "", "episodic": "", "working": {}}


class TestWorkingMemory:
    def test_add_working(self, store):
        store.add("color: blue", category="working")
        assert store.get_working() == {"color": "blue"}

    def test_add_working_no_colon(self, store):
        store.add("just a note", category="working")
        wm = store.get_working()
        assert "just a note" in wm

    def test_get_working_by_key(self, store):
        store.add("key: value", category="working")
        assert store.get_working("key") == "value"

    def test_clear_working(self, store):
        store.add("x: 1", category="working")
        store.clear_working()
        assert store.get_working() == {}

    def test_working_not_persistent(self, store):
        store.add("temp: data", category="working")
        store2 = MemoryStore(memory_dir=os.path.dirname(store._memory_file))
        assert store2.get_working() == {}

class TestEpisodicMemory:
    def test_add_episodic(self, store):
        store.add("完成了 Phase 10 D", category="episodic")
        snapshot = store.for_system_prompt()
        assert "Phase 10 D" in snapshot
        assert "历史会话摘要" in snapshot

    def test_episodic_persists(self, store):
        store.add("第一次会话", category="episodic")
        store2 = MemoryStore(memory_dir=os.path.dirname(store._memory_file))
        snapshot = store2.for_system_prompt()
        assert "第一次会话" in snapshot

    def test_episodic_append(self, store):
        store.add("第一条", category="episodic")
        store.add("第二条", category="episodic")
        snapshot = store.for_system_prompt()
        assert "第一条" in snapshot
        assert "第二条" in snapshot

    def test_summarize_to_episodic(self, store):
        store.summarize_to_episodic("E 阶段完成")
        snapshot = store.for_system_prompt()
        assert "E 阶段完成" in snapshot

