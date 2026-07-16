"""工具熔断器模块测试"""

import time
from utils.circuit_breaker import CircuitBreaker, ToolCircuitBreakerManager, CircuitState


class TestCircuitBreaker:
    """熔断器测试"""

    def test_initial_state(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_open_after_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.2)
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_close_after_success_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1,
                           half_open_max_calls=3, success_threshold=2)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.2)
        cb.can_execute()  # 进入 HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_open_after_failure_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.2)
        cb.can_execute()  # 进入 HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_reduces_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        cb.record_success()
        assert cb.failure_count == 1

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


class TestToolCircuitBreakerManager:
    """工具熔断器管理器测试"""

    def test_get_breaker(self):
        mgr = ToolCircuitBreakerManager()
        cb = mgr.get_breaker("file")
        assert isinstance(cb, CircuitBreaker)

    def test_same_breaker_for_same_tool(self):
        mgr = ToolCircuitBreakerManager()
        cb1 = mgr.get_breaker("file")
        cb2 = mgr.get_breaker("file")
        assert cb1 is cb2

    def test_different_breakers_for_different_tools(self):
        mgr = ToolCircuitBreakerManager()
        cb1 = mgr.get_breaker("file")
        cb2 = mgr.get_breaker("web")
        assert cb1 is not cb2

    def test_can_execute(self):
        mgr = ToolCircuitBreakerManager(default_failure_threshold=2)
        assert mgr.can_execute("file") is True
        mgr.record_failure("file")
        mgr.record_failure("file")
        assert mgr.can_execute("file") is False

    def test_record_success(self):
        mgr = ToolCircuitBreakerManager(default_failure_threshold=2)
        mgr.record_failure("file")
        mgr.record_success("file")
        assert mgr.get_breaker("file").failure_count == 0

    def test_reset_single(self):
        mgr = ToolCircuitBreakerManager(default_failure_threshold=2)
        mgr.record_failure("file")
        mgr.record_failure("file")  # file 达到阈值
        mgr.record_failure("web")
        mgr.record_failure("web")  # web 达到阈值
        mgr.reset("file")
        assert mgr.get_breaker("file").state == CircuitState.CLOSED
        assert mgr.get_breaker("web").state == CircuitState.OPEN

    def test_reset_all(self):
        mgr = ToolCircuitBreakerManager(default_failure_threshold=2)
        mgr.record_failure("file")
        mgr.record_failure("web")
        mgr.reset()
        assert mgr.get_breaker("file").state == CircuitState.CLOSED
        assert mgr.get_breaker("web").state == CircuitState.CLOSED

    def test_get_open_circuits(self):
        mgr = ToolCircuitBreakerManager(default_failure_threshold=2)
        mgr.record_failure("file")
        mgr.record_failure("file")
        mgr.record_failure("web")
        assert mgr.get_open_circuits() == ["file"]

    def test_get_all_states(self):
        mgr = ToolCircuitBreakerManager(default_failure_threshold=2)
        mgr.record_failure("file")
        states = mgr.get_all_states()
        assert "file" in states
        assert states["file"]["state"] == "closed"  # 还没达到阈值
