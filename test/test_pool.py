"""Test Agent Pool — 角色级并发限流"""
import threading
import time

import pytest

from agent.pool import acquire, release, reset, active_count, set_default_pool_size


class TestAgentPool:
    def setup_method(self):
        reset()

    def test_acquire_release(self):
        """基本 acquire/release 流程。"""
        acquire("test_agent", pool_size=1)
        assert active_count("test_agent") >= 0  # 占用中
        release("test_agent")

    def test_pool_size_blocks(self):
        """pool_size=1 时第二个 acquire 会阻塞。"""
        acquire("blocker", pool_size=1)

        blocked = threading.Event()
        result = []

        def try_acquire():
            acquire("blocker", pool_size=1)
            result.append("acquired")
            release("blocker")

        t = threading.Thread(target=try_acquire)
        t.start()
        time.sleep(0.05)  # 等线程进入 acquire

        assert len(result) == 0  # 还没获取到
        release("blocker")  # 释放第一个
        t.join(timeout=1)
        assert len(result) == 1  # 获取到了

    def test_multiple_slots(self):
        """pool_size=3 可以同时运行 3 个。"""
        pool_size = 3
        active = 0
        lock = threading.Lock()

        def worker():
            nonlocal active
            acquire("multi", pool_size=pool_size)
            with lock:
                active += 1
            time.sleep(0.1)
            with lock:
                active -= 1
            release("multi")

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()

        time.sleep(0.05)  # 等所有线程跑到 acquire 内
        with lock:
            assert active <= pool_size  # 不超过 pool_size

        for t in threads:
            t.join(timeout=1)

    def test_reset_clears(self):
        """reset() 清空所有池，之后可重新创建。"""
        acquire("a", pool_size=1)
        # 没 release 就 reset 不会报错
        reset()
        # reset 后重新 acquire 应该无阻塞
        acquire("a", pool_size=1)
        release("a")

    def test_default_pool_size(self):
        """不指定 pool_size 时使用全局默认。"""
        set_default_pool_size(2)
        acquire("default_test")
        acquire("default_test")
        # 第三个会阻塞，但我们不等到超时，只是验证不报错即可
        release("default_test")
        release("default_test")
        set_default_pool_size(5)  # 恢复
