"""memory/retrieval.py 测试 — FTS5WeightedRetrieval（方案 A）"""

import os
import tempfile

import pytest

from memory.retrieval import FTS5WeightedRetrieval, RetrievalStrategy, _compute_score


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    r = FTS5WeightedRetrieval(path)
    yield r
    for ext in ("", "-wal", "-shm"):
        p = path + ext
        if os.path.exists(p):
            os.unlink(p)


class TestScoreFunction:
    def test_high_bm25_gives_low_score(self):
        """BM25 负数绝对值越大（分越低），norm_bm25 越接近 0。"""
        score = _compute_score(bm25_score=-50, weight=0.5, age_days=0)
        assert 0 < score <= 1

    def test_weight_bonus(self):
        high_weight = _compute_score(0, weight=1.0, age_days=0)
        low_weight = _compute_score(0, weight=0.0, age_days=0)
        assert high_weight > low_weight

    def test_temporal_decay(self):
        fresh = _compute_score(0, weight=0.5, age_days=0)
        old = _compute_score(0, weight=0.5, age_days=60)
        assert fresh > old

    def test_all_factors_combined(self):
        """验证组合分数在 0~1 范围内。"""
        for bm25 in [-1, -10, -50]:
            for w in [0, 0.5, 1.0]:
                for d in [0, 30, 365]:
                    s = _compute_score(bm25, w, d)
                    assert 0.0 <= s <= 1.0, f"score={s} out of range"


class TestProtocolConformance:
    def test_is_retrieval_strategy(self, store):
        assert isinstance(store, RetrievalStrategy)


class TestFTS5CRUD:
    def test_add_and_count(self, store):
        store.add_text("first memory")
        store.add_text("second memory")
        assert store.count() == 2

    def test_add_with_metadata(self, store):
        store.add_text("important memory", metadata={"weight": 0.9, "category": "memory", "source": "user"})
        assert store.count() == 1

    def test_remove(self, store):
        id1 = store.add_text("keep")
        id2 = store.add_text("delete")
        assert store.count() == 2
        store.remove(id2)
        assert store.count() == 1

    def test_empty_initial(self, store):
        assert store.count() == 0


class TestFTS5Search:
    def test_basic_search(self, store):
        store.add_text("I like Python programming")
        store.add_text("The weather is nice today")
        store.add_text("Python is the best language")

        results = store.search("Python")
        assert len(results) >= 1
        assert "Python" in results[0]["text"]

    def test_no_match(self, store):
        store.add_text("hello world")
        assert store.search("xyz") == []

    def test_empty_query(self, store):
        store.add_text("hello world")
        assert store.search("") == []
        assert store.search("  ") == []

    def test_top_k_limits(self, store):
        for i in range(10):
            store.add_text(f"memory entry {i}")
        results = store.search("entry", top_k=3)
        assert len(results) <= 3

    def test_weight_higher_rank(self, store):
        """权重高的记忆排在更前面（其他条件相同时）。"""
        store.add_text("normal entry", metadata={"weight": 0.3})
        store.add_text("important entry", metadata={"weight": 1.0})

        results = store.search("entry", top_k=5)
        assert len(results) >= 2
        # 权重高的应该得分更高
        high_w = next(r for r in results if "important" in r["text"])
        low_w = next(r for r in results if "normal" in r["text"])
        assert high_w["score"] >= low_w["score"]


class TestTemporalDecay:
    def test_recent_higher_score(self, store):
        """最近添加的记忆有更高分数（时间衰减）。"""
        sid_old = store.add_text("memory content")
        sid_new = store.add_text("memory content")

        # 直接修改 created_at，使两条数据相隔 30 天
        import time
        now = time.time()
        with store._connect() as conn:
            conn.execute("UPDATE retrieval SET created_at = ? WHERE id = ?", (now - 86400 * 30, sid_old))
            conn.execute("UPDATE retrieval SET created_at = ? WHERE id = ?", (now, sid_new))

        results = store.search("memory", top_k=5)
        assert len(results) >= 2
        # 新的排在前面（时间衰减）
        assert results[0]["id"] == sid_new

    def test_decay_parameter_effect(self):
        """较短的半衰期使衰减更剧烈。"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path1 = f.name
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path2 = f.name

        r1 = FTS5WeightedRetrieval(path1, decay_half_life=1)  # 快速衰减
        r2 = FTS5WeightedRetrieval(path2, decay_half_life=365)  # 慢速衰减

        r1.add_text("test content")
        r2.add_text("test content")

        # 模拟数据变老：直接修改 created_at
        import time
        old_time = time.time() - 86400 * 30  # 30 天前
        for r in (r1, r2):
            with r._connect() as conn:
                conn.execute("UPDATE retrieval SET created_at = ? WHERE id = 1", (old_time,))

        s1 = r1.search("test")
        s2 = r2.search("test")

        # 快速衰减的分数应该更低
        assert s1[0]["score"] < s2[0]["score"]

        for ext in ("", "-wal", "-shm"):
            for p in (path1, path2):
                q = p + ext
                if os.path.exists(q):
                    os.unlink(q)


class TestPersistence:
    def test_reopen_preserves_data(self, store):
        store.add_text("persistent data", metadata={"weight": 0.8})
        path = store._db_path

        store2 = FTS5WeightedRetrieval(path)
        assert store2.count() == 1
        results = store2.search("persistent")
        assert len(results) == 1
        assert results[0]["weight"] == 0.8
