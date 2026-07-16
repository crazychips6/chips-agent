"""多级工具结果缓存

L1: 内存 LRU 缓存（热数据）
L2: TTL 缓存（温数据）

支持：
- 自动过期
- LRU 淘汰
- 按工具名失效
- 缓存统计
"""

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CacheEntry:
    """缓存条目"""
    value: Any
    timestamp: float
    access_count: int = 0


class L1Cache:
    """L1 内存 LRU 缓存"""

    def __init__(self, max_size: int = 100):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                entry.access_count += 1
                # 移到末尾（最近使用）
                self._cache.move_to_end(key)
                return entry.value
            return None

    def set(self, key: str, value: Any):
        """设置缓存值"""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key].value = value
                self._cache[key].timestamp = time.time()
            else:
                if len(self._cache) >= self._max_size:
                    # 淘汰最旧的
                    self._cache.popitem(last=False)
                self._cache[key] = CacheEntry(
                    value=value,
                    timestamp=time.time()
                )

    def invalidate(self, key: str):
        """使缓存失效"""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """返回缓存大小"""
        with self._lock:
            return len(self._cache)


class L2Cache:
    """L2 TTL 缓存"""

    def __init__(self, ttl: float = 300.0):
        self._cache: dict[str, CacheEntry] = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() - entry.timestamp < self._ttl:
                    return entry.value
                else:
                    del self._cache[key]
            return None

    def set(self, key: str, value: Any):
        """设置缓存值"""
        with self._lock:
            self._cache[key] = CacheEntry(
                value=value,
                timestamp=time.time()
            )

    def invalidate(self, key: str):
        """使缓存失效"""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()

    def cleanup(self):
        """清理过期条目"""
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._cache.items()
                       if now - v.timestamp >= self._ttl]
            for k in expired:
                del self._cache[k]

    def size(self) -> int:
        """返回缓存大小"""
        with self._lock:
            return len(self._cache)


@dataclass
class CacheStats:
    """缓存统计"""
    l1_hits: int = 0
    l1_misses: int = 0
    l2_hits: int = 0
    l2_misses: int = 0
    total_sets: int = 0
    total_invalidations: int = 0

    @property
    def hit_rate(self) -> float:
        """总命中率"""
        total = self.l1_hits + self.l1_misses + self.l2_hits + self.l2_misses
        if total == 0:
            return 0.0
        return (self.l1_hits + self.l2_hits) / total

    def to_dict(self) -> dict:
        return {
            "l1_hits": self.l1_hits,
            "l1_misses": self.l1_misses,
            "l2_hits": self.l2_hits,
            "l2_misses": self.l2_misses,
            "hit_rate": f"{self.hit_rate:.2%}",
            "total_sets": self.total_sets,
            "total_invalidations": self.total_invalidations,
        }


class ToolResultCache:
    """多级工具结果缓存"""

    def __init__(self, l1_size: int = 100, l2_ttl: float = 300.0):
        """
        Args:
            l1_size: L1 缓存最大条目数
            l2_ttl: L2 缓存 TTL（秒）
        """
        self._l1 = L1Cache(max_size=l1_size)
        self._l2 = L2Cache(ttl=l2_ttl)
        self._stats = CacheStats()
        self._lock = threading.Lock()

    @staticmethod
    def _make_key(tool_name: str, args: dict) -> str:
        """生成缓存键"""
        args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
        args_hash = hashlib.md5(args_str.encode()).hexdigest()[:12]
        return f"{tool_name}:{args_hash}"

    def get(self, tool_name: str, args: dict) -> Optional[Any]:
        """获取缓存结果"""
        key = self._make_key(tool_name, args)

        # L1 命中
        result = self._l1.get(key)
        if result is not None:
            with self._lock:
                self._stats.l1_hits += 1
            return result

        with self._lock:
            self._stats.l1_misses += 1

        # L2 命中
        result = self._l2.get(key)
        if result is not None:
            with self._lock:
                self._stats.l2_hits += 1
            # 提升到 L1
            self._l1.set(key, result)
            return result

        with self._lock:
            self._stats.l2_misses += 1

        return None

    def set(self, tool_name: str, args: dict, value: Any):
        """设置缓存"""
        key = self._make_key(tool_name, args)

        # 写入 L1
        self._l1.set(key, value)
        # 写入 L2
        self._l2.set(key, value)

        with self._lock:
            self._stats.total_sets += 1

    def invalidate(self, tool_name: Optional[str] = None):
        """使缓存失效

        Args:
            tool_name: 工具名，为 None 时清空所有缓存
        """
        if tool_name is None:
            self._l1.clear()
            self._l2.clear()
        else:
            # 通过前缀匹配失效
            # L1: 遍历删除
            with self._l1._lock:
                keys_to_delete = [k for k in self._l1._cache.keys()
                                  if k.startswith(tool_name)]
                for k in keys_to_delete:
                    del self._l1._cache[k]

            # L2: 清理过期后删除
            self._l2.cleanup()
            with self._l2._lock:
                keys_to_delete = [k for k in self._l2._cache.keys()
                                  if k.startswith(tool_name)]
                for k in keys_to_delete:
                    del self._l2._cache[k]

        with self._lock:
            self._stats.total_invalidations += 1

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._lock:
            stats = self._stats.to_dict()
        stats["l1_size"] = self._l1.size()
        stats["l2_size"] = self._l2.size()
        return stats

    def clear_stats(self):
        """清空统计"""
        with self._lock:
            self._stats = CacheStats()
