"""memory/vector.py 测试

使用临时文件避免污染持久化数据。"""

import os
import tempfile

import pytest

from memory.vector import VectorStore, cosine_similarity


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    vs = VectorStore(path)
    yield vs
    vs._connect().close()  # ensure clean close
    for ext in ("", "-wal", "-shm"):
        p = path + ext
        if os.path.exists(p):
            os.unlink(p)


class TestCosineSimilarity:
    def test_identical(self):
        assert cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0

    def test_opposite(self):
        assert cosine_similarity([1, 0], [-1, 0]) == -1.0

    def test_orthogonal(self):
        assert cosine_similarity([1, 0], [0, 1]) == 0.0

    def test_partial(self):
        sim = cosine_similarity([1, 2, 3], [1, 2, 3])
        assert abs(sim - 1.0) < 0.001

    def test_zero_vector(self):
        assert cosine_similarity([0, 0], [1, 0]) == 0.0

    def test_different_lengths_truncated(self):
        """不同长度的向量 zip 会静默截断。"""
        from memory.vector import cosine_similarity
        # 行为上 zip 截断较短的一个，这个测试只验证不崩溃
        cosine_similarity([1, 0], [1, 0, 999])  # 不会崩溃


class TestVectorStoreCRUD:
    def test_add_and_count(self, store):
        e1 = [0.1, 0.2, 0.3]
        e2 = [0.4, 0.5, 0.6]
        store.add("test", "hello", e1)
        store.add("test", "world", e2)
        assert store.count("test") == 2

    def test_count_all(self, store):
        store.add("ns1", "a", [1.0, 0.0])
        store.add("ns2", "b", [0.0, 1.0])
        assert store.count() == 2

    def test_delete(self, store):
        e = [1.0, 0.0, 0.0]
        store.add("test", "to_delete", e)
        sid = store.add("test", "keep", e)
        assert store.count("test") == 2
        store.delete(sid)
        assert store.count("test") == 1

    def test_empty_store_returns_empty(self, store):
        assert store.search("nonexistent", [1.0, 0.0], top_k=5) == []

    def test_list_namespaces(self, store):
        assert store.list_namespaces() == []
        store.add("mem", "x", [1.0])
        store.add("epi", "y", [1.0])
        namespaces = store.list_namespaces()
        assert "mem" in namespaces
        assert "epi" in namespaces

    def test_delete_namespace(self, store):
        store.add("ns1", "a", [1.0])
        store.add("ns1", "b", [1.0])
        store.add("ns2", "c", [1.0])
        store.delete_namespace("ns1")
        assert store.count("ns1") == 0
        assert store.count("ns2") == 1


class TestVectorStoreSearch:
    def test_most_similar_first(self, store):
        """相关度最高的结果排在最前面。"""
        store.add("test", "我喜欢编程", [0.9, 0.1, 0.0])
        store.add("test", "我喜欢做菜", [0.1, 0.9, 0.0])
        store.add("test", "Python 很好用", [0.8, 0.2, 0.1])

        results = store.search("test", [0.85, 0.15, 0.05], top_k=3)
        assert len(results) == 3
        # 第一条应该是最相关的 "我喜欢编程" 或 "Python 很好用"
        assert results[0]["score"] >= results[1]["score"]
        assert results[1]["score"] >= results[2]["score"]

    def test_top_k_limits(self, store):
        for i in range(10):
            store.add("test", f"item {i}", [float(i) / 10, 0.0])

        results = store.search("test", [1.0, 0.0], top_k=3)
        assert len(results) == 3

    def test_namespace_isolation(self, store):
        emb = [1.0, 0.0]
        store.add("ns1", "ns1 text", emb)
        store.add("ns2", "ns2 text", emb)

        results = store.search("ns1", emb, top_k=5)
        assert len(results) == 1
        assert results[0]["namespace"] == "ns1"

    def test_scores_in_range(self, store):
        emb = [0.5, 0.5]
        store.add("test", "some text", emb)

        results = store.search("test", emb, top_k=5)
        assert len(results) == 1
        assert -1.0 <= results[0]["score"] <= 1.0


class TestVectorStoreBatch:
    def test_add_batch(self, store):
        items = [
            ("a", [1.0, 0.0], {"source": "test"}),
            ("b", [0.0, 1.0], None),
        ]
        ids = store.add_batch("test", items)
        assert len(ids) == 2
        assert store.count("test") == 2

    def test_empty_batch(self, store):
        assert store.add_batch("test", []) == []


class TestVectorStorePersistence:
    def test_reopen_preserves_data(self, store):
        """关闭后重新打开向量数据仍在。"""
        emb = [0.5, 0.5]
        store.add("test", "persistent data", emb)
        path = store._db_path

        store2 = VectorStore(path)
        results = store2.search("test", emb, top_k=5)
        assert len(results) == 1
        assert results[0]["text"] == "persistent data"

    def test_empty_db_init_no_crash(self):
        """不存在的路径也能正常初始化。"""
        import shutil
        path = "/tmp/__nonexistent__/vectors.db"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            vs = VectorStore(path)
            assert vs.count() == 0
        finally:
            os.unlink(path)
            shutil.rmtree(os.path.dirname(path))
