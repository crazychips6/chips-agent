"""agent.retry 模块测试"""

from agent.retry import jittered_backoff


class TestJitteredBackoff:
    def test_attempt_1(self):
        delay = jittered_backoff(1)
        # base_delay=5, jitter_ratio=0.5 → 5 + [0, 2.5)
        assert 5.0 <= delay < 7.5

    def test_attempt_2(self):
        delay = jittered_backoff(2)
        # 5*2=10 + [0, 5)
        assert 10.0 <= delay < 15.0

    def test_attempt_3(self):
        delay = jittered_backoff(3)
        # 5*4=20 + [0, 10)
        assert 20.0 <= delay < 30.0

    def test_capped_at_max_delay(self):
        delay = jittered_backoff(100)
        # 基值封顶 120，jitter [0, 60)，结果在 [120, 180)
        assert 120.0 <= delay < 180.0

    def test_custom_base_delay(self):
        delay = jittered_backoff(1, base_delay=1.0)
        # 1 + [0, 0.5)
        assert 1.0 <= delay < 1.5

    def test_custom_max_delay(self):
        delay = jittered_backoff(10, base_delay=1.0, max_delay=10.0)
        # attempt 10: 1*512=512 → capped at 10
        assert 10.0 <= delay < 15.0

    def test_zero_base_delay(self):
        delay = jittered_backoff(1, base_delay=0.0)
        # base_delay<=0 → 直接用 max_delay=120 → [120, 180)
        assert 120.0 <= delay < 180.0

    def test_jitter_disabled(self):
        """jitter_ratio=0 时 jitter 为 0。"""
        for attempt in range(1, 5):
            delay = jittered_backoff(attempt, jitter_ratio=0.0)
            expected = min(5.0 * (2 ** (attempt - 1)), 120.0)
            assert delay == expected

    def test_jitter_range(self):
        """jitter 值不会超出 [0, jitter_ratio*delay] 范围。"""
        for attempt in range(1, 10):
            delay = jittered_backoff(attempt)
            base = min(5.0 * (2 ** (attempt - 1)), 120.0)
            jitter = delay - base
            assert 0 <= jitter <= 0.5 * base

    def test_jitter_variety(self):
        """多次调用返回值不完全相同（随机性校验）。"""
        delays = {jittered_backoff(2) for _ in range(100)}
        assert len(delays) > 1  # 不太可能 100 次随机都一样

    def test_monotonic_without_jitter(self):
        """jitter_ratio=0 时 attempt 越大延迟越大（封顶后持平）。"""
        previous = jittered_backoff(1, jitter_ratio=0.0)
        for attempt in range(2, 10):
            current = jittered_backoff(attempt, jitter_ratio=0.0)
            assert current >= previous
            previous = current
