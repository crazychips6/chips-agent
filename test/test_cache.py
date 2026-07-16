"""工具缓存模块测试"""

import time
from utils.cache import ToolResultCache, L1Cache, L2Cache, CacheStats


class TestL1Cache:
    """L1 内存缓存测试"""

    def test_basic_get_set(self):
        cache = L1Cache(max_size=10)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_lru_eviction(self):
        cache = L1Cache(max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # 访问 a，使其成为最近使用
        cache.get("a")
        # 添加 d，应该淘汰 b
        cache.set("d", 4)
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_invalidate(self):
        cache = L1Cache(max_size=10)
        cache.set("key1", "value1")
        cache.invalidate("key1")
        assert cache.get("key1") is None

    def test_clear(self):
        cache = L1Cache(max_size=10)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.size() == 0


class TestL2Cache:
    """L2 TTL 缓存测试"""

    def test_basic_get_set(self):
        cache = L2Cache(ttl=1.0)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_ttl_expiration(self):
        cache = L2Cache(ttl=0.1)
        cache.set("key1", "value1")
        time.sleep(0.2)
        assert cache.get("key1") is None

    def test_cleanup(self):
        cache = L2Cache(ttl=0.1)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        time.sleep(0.2)
        cache.cleanup()
        assert cache.size() == 0


class TestToolResultCache:
    """工具结果缓存测试"""

    def test_basic_cache(self):
        cache = ToolResultCache(l1_size=10, l2_ttl=60.0)
        cache.set("file", {"path": "/tmp/test"}, "file content")
        result = cache.get("file", {"path": "/tmp/test"})
        assert result == "file content"

    def test_different_args(self):
        cache = ToolResultCache(l1_size=10, l2_ttl=60.0)
        cache.set("file", {"path": "/tmp/a"}, "content a")
        cache.set("file", {"path": "/tmp/b"}, "content b")
        assert cache.get("file", {"path": "/tmp/a"}) == "content a"
        assert cache.get("file", {"path": "/tmp/b"}) == "content b"

    def test_invalidate_by_tool(self):
        cache = ToolResultCache(l1_size=10, l2_ttl=60.0)
        cache.set("file", {"path": "/tmp/a"}, "content a")
        cache.set("web", {"url": "http://test.com"}, "web content")
        cache.invalidate("file")
        assert cache.get("file", {"path": "/tmp/a"}) is None
        assert cache.get("web", {"url": "http://test.com"}) == "web content"

    def test_invalidate_all(self):
        cache = ToolResultCache(l1_size=10, l2_ttl=60.0)
        cache.set("file", {"path": "/tmp/a"}, "content a")
        cache.set("web", {"url": "http://test.com"}, "web content")
        cache.invalidate()
        assert cache.get("file", {"path": "/tmp/a"}) is None
        assert cache.get("web", {"url": "http://test.com"}) is None

    def test_stats(self):
        cache = ToolResultCache(l1_size=10, l2_ttl=60.0)
        cache.set("file", {"path": "/tmp/a"}, "content a")
        cache.get("file", {"path": "/tmp/a"})  # hit
        cache.get("file", {"path": "/tmp/b"})  # miss
        stats = cache.get_stats()
        assert stats["l1_hits"] == 1
        assert stats["l2_misses"] == 1
        assert stats["total_sets"] == 1

    def test_l1_promotion(self):
        """测试 L2 命中后提升到 L1"""
        cache = ToolResultCache(l1_size=10, l2_ttl=60.0)
        cache.set("file", {"path": "/tmp/a"}, "content a")
        # 从 L1 移除（模拟 L1 已满）
        cache._l1.clear()
        # L2 仍然有
        result = cache.get("file", {"path": "/tmp/a"})
        assert result == "content a"
        # 现在应该在 L1 中
        assert cache._l1.size() == 1
