"""工具调用熔断器

防止工具持续失败导致雪崩效应。

状态机：
- CLOSED: 正常状态，允许执行
- OPEN: 熔断状态，拒绝执行
- HALF_OPEN: 半开状态，允许少量探测请求

参考：Martin Fowler - Circuit Breaker Pattern
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"        # 正常
    OPEN = "open"            # 熔断
    HALF_OPEN = "half_open"  # 半开


@dataclass
class CircuitBreaker:
    """熔断器

    Args:
        failure_threshold: 连续失败次数阈值
        recovery_timeout: 熔断恢复超时（秒）
        half_open_max_calls: 半开状态最大探测调用数
        success_threshold: 半开状态恢复到 CLOSED 所需的成功次数
    """
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 3
    success_threshold: int = 2

    # 内部状态
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    success_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    half_open_calls: int = field(default=0, init=False)

    def __post_init__(self):
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        """判断是否可以执行"""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                # 检查是否超过恢复超时
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    self.success_count = 0
                    return True
                return False

            if self.state == CircuitState.HALF_OPEN:
                return self.half_open_calls < self.half_open_max_calls

            return False

    def record_success(self):
        """记录成功"""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                # 成功时减少失败计数
                self.failure_count = max(0, self.failure_count - 1)

            elif self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    # 恢复到 CLOSED
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0

    def record_failure(self):
        """记录失败"""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.state == CircuitState.HALF_OPEN:
                # 半开状态失败，重新熔断
                self.state = CircuitState.OPEN
                self.half_open_calls = 0

            elif self.state == CircuitState.CLOSED:
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN

    def reset(self):
        """重置熔断器"""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = 0.0
            self.half_open_calls = 0

    def get_state_info(self) -> dict:
        """获取状态信息"""
        with self._lock:
            return {
                "state": self.state.value,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
                "last_failure_time": self.last_failure_time,
                "half_open_calls": self.half_open_calls,
            }


class ToolCircuitBreakerManager:
    """工具熔断器管理器"""

    def __init__(self, default_failure_threshold: int = 5,
                 default_recovery_timeout: float = 60.0):
        """
        Args:
            default_failure_threshold: 默认失败阈值
            default_recovery_timeout: 默认恢复超时
        """
        self._breakers: dict[str, CircuitBreaker] = {}
        self._default_failure_threshold = default_failure_threshold
        self._default_recovery_timeout = default_recovery_timeout
        self._lock = threading.Lock()

    def get_breaker(self, tool_name: str,
                    failure_threshold: Optional[int] = None,
                    recovery_timeout: Optional[float] = None) -> CircuitBreaker:
        """获取工具熔断器

        如果不存在则创建
        """
        with self._lock:
            if tool_name not in self._breakers:
                self._breakers[tool_name] = CircuitBreaker(
                    failure_threshold=failure_threshold or self._default_failure_threshold,
                    recovery_timeout=recovery_timeout or self._default_recovery_timeout,
                )
            return self._breakers[tool_name]

    def can_execute(self, tool_name: str) -> bool:
        """判断工具是否可以执行"""
        return self.get_breaker(tool_name).can_execute()

    def record_success(self, tool_name: str):
        """记录成功"""
        self.get_breaker(tool_name).record_success()

    def record_failure(self, tool_name: str):
        """记录失败"""
        self.get_breaker(tool_name).record_failure()

    def reset(self, tool_name: Optional[str] = None):
        """重置熔断器

        Args:
            tool_name: 工具名，为 None 时重置所有
        """
        with self._lock:
            if tool_name is None:
                for breaker in self._breakers.values():
                    breaker.reset()
            else:
                if tool_name in self._breakers:
                    self._breakers[tool_name].reset()

    def get_all_states(self) -> dict:
        """获取所有工具的熔断器状态"""
        with self._lock:
            return {
                name: breaker.get_state_info()
                for name, breaker in self._breakers.items()
            }

    def get_open_circuits(self) -> list:
        """获取所有熔断中的工具"""
        with self._lock:
            return [
                name for name, breaker in self._breakers.items()
                if breaker.state == CircuitState.OPEN
            ]
