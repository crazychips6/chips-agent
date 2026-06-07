"""TokenBucket — 令牌桶限流

支持 acquire (非阻塞) / wait (阻塞) 两种模式。
通常作为 wrapper 装饰 ModelGateway。"""

from __future__ import annotations

import time


class TokenBucket:
    """令牌桶。

    Args:
        rate: 每秒恢复的令牌数。
        capacity: 桶容量（最大突发）。
    """

    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def acquire(self, tokens: float = 1.0) -> bool:
        """非阻塞获取令牌。返回 True 表示获取成功。"""
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def wait(self, tokens: float = 1.0) -> None:
        """阻塞直到获取到足够令牌。"""
        while not self.acquire(tokens):
            sleep_time = (tokens - self.tokens) / self.rate if self.rate > 0 else 1.0
            time.sleep(min(sleep_time, 0.1))
            self._refill()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now
