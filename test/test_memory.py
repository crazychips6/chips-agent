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


# ── B3: 向量检索 + 自动 embedding ──


class TestPrefetch:
    def test_prefetch_without_embedding_fallback(self, store):
        """无 embedding 服务时 prefetch 降级为返回全部 memory。"""
        store.add("降级测试数据", category="memory")
        result = store.prefetch("任何查询")
        assert "降级测试数据" in result

    def test_prefetch_with_embedding(self):
        """有 embedding 服务时返回检索结果。"""
        from unittest.mock import MagicMock

        emb_mock = MagicMock()
        emb_mock.embed.return_value = [[0.1, 0.2, 0.3]]

        import tempfile
        tmpdir = tempfile.mkdtemp()

        from memory.vector import VectorStore
        vs_path = os.path.join(tmpdir, "vectors.db")
        vs = VectorStore(vs_path)
        vs.add("memory", "向量记忆内容", [0.1, 0.2, 0.3])

        ms = MemoryStore(memory_dir=tmpdir, embedding_service=emb_mock, vector_store=vs)
        result = ms.prefetch("查询", top_k=5)
        assert "向量记忆内容" in result

    def test_prefetch_empty_vector_store(self, store):
        """向量存储为空时回退到文件快照。"""
        from unittest.mock import MagicMock
        store._embedding = MagicMock()
        store._embedding.embed.return_value = [[1.0, 0.0]]

        import tempfile
        from memory.vector import VectorStore
        vs_path = os.path.join(tempfile.mkdtemp(), "v.db")
        store._vector_store = VectorStore(vs_path)

        store.add("文件中的记忆", category="memory")
        result = store.prefetch("查询")
        assert "文件中的记忆" in result

    def test_prefetch_embedding_failure_safe(self, store):
        """embedding 失败时静默降级。"""
        from unittest.mock import MagicMock
        emb_mock = MagicMock()
        emb_mock.embed.side_effect = Exception("API 错误")
        store._embedding = emb_mock

        store.add("出错也有降级", category="memory")
        result = store.prefetch("查询")
        assert "出错也有降级" in result

    def test_prefetch_no_prefix_header(self, store):
        """prefetch 不再自带自然语言前缀头。"""
        store.add("content", category="memory")
        result = store.prefetch("content")
        # 结果不应含 "根据当前上下文检索" 之类的头
        assert "检索到" not in result
        assert "相关记忆" not in result


class TestGetContext:
    def test_get_context_returns_all_keys(self, store):
        ctx = store.get_context()
        assert set(ctx.keys()) == {"memory", "user", "episodic", "working"}

    def test_get_context_without_query_returns_snapshot(self, store):
        store.add("snapshot content", category="memory")
        ctx = store.get_context()
        assert "snapshot content" in ctx["memory"]

    def test_get_context_with_query_calls_prefetch(self, store):
        store.add("prefetch content", category="memory")
        ctx = store.get_context(query="prefetch")
        assert "prefetch content" in ctx["memory"]

    def test_get_context_returns_user_and_episodic(self, store):
        store.add("user info", category="user")
        store.add("session summary", category="episodic")
        ctx = store.get_context()
        assert "user info" in ctx["user"]
        assert "session summary" in ctx["episodic"]

    def test_get_context_working_is_dict_or_none(self, store):
        ctx = store.get_context()
        # 没有工作记忆时为 None
        assert ctx["working"] is None

        store.add("key: val", category="working")
        ctx = store.get_context()
        assert ctx["working"] == {"key": "val"}

    def test_get_context_query_none_equals_no_query(self, store):
        store.add("memory content", category="memory")
        ctx_none = store.get_context(query=None)
        ctx_noarg = store.get_context()
        assert ctx_none == ctx_noarg


class TestAutoEmbed:
    def test_add_memory_triggers_auto_embed(self):
        """add(memory) 自动触发 embedding。"""
        from unittest.mock import MagicMock
        import tempfile

        tmpdir = tempfile.mkdtemp()
        emb_mock = MagicMock()
        emb_mock.embed.return_value = [[0.5, 0.5]]

        from memory.vector import VectorStore
        vs_path = os.path.join(tmpdir, "v.db")
        vs = VectorStore(vs_path)

        ms = MemoryStore(memory_dir=tmpdir, embedding_service=emb_mock, vector_store=vs)
        ms.add("自动嵌入的数据", category="memory")

        # 验证 embedding 被调用
        emb_mock.embed.assert_called_once()
        # 验证存储了数据
        assert vs.count("memory") == 1

    def test_add_episodic_triggers_auto_embed(self):
        """add(episodic) 自动触发 embedding。"""
        from unittest.mock import MagicMock
        import tempfile

        tmpdir = tempfile.mkdtemp()
        emb_mock = MagicMock()
        emb_mock.embed.return_value = [[0.5, 0.5]]

        from memory.vector import VectorStore
        vs_path = os.path.join(tmpdir, "v.db")
        vs = VectorStore(vs_path)

        ms = MemoryStore(memory_dir=tmpdir, embedding_service=emb_mock, vector_store=vs)
        ms.add("会话摘要", category="episodic")

        assert vs.count("episodic") == 1

    def test_add_working_does_not_embed(self):
        """working 类别不应触发 embedding。"""
        from unittest.mock import MagicMock
        import tempfile

        tmpdir = tempfile.mkdtemp()
        emb_mock = MagicMock()
        emb_mock.embed.return_value = [[0.5, 0.5]]

        from memory.vector import VectorStore
        vs_path = os.path.join(tmpdir, "v.db")
        vs = VectorStore(vs_path)

        ms = MemoryStore(memory_dir=tmpdir, embedding_service=emb_mock, vector_store=vs)
        ms.add("color: blue", category="working")

        emb_mock.embed.assert_not_called()
        assert vs.count() == 0

    def test_auto_embed_failure_does_not_block(self, store):
        """embedding 失败不阻塞 add 主流程。"""
        from unittest.mock import MagicMock
        emb_mock = MagicMock()
        emb_mock.embed.side_effect = Exception("API 挂了")
        store._embedding = emb_mock

        result = store.add("即使 embedding 失败也要保存", category="memory")
        assert result["status"] == "ok"

        snapshot = store.for_system_prompt()
        assert "即使 embedding 失败也要保存" in snapshot

