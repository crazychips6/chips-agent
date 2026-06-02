"""Retry utilities — jittered exponential backoff

纯函数，无外部依赖，独立可测。"""

import random
import time


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """计算带 jitter 的指数退避延迟。

    Args:
        attempt: 从 1 开始的重试次数。
        base_delay: 基础延迟秒数。
        max_delay: 延迟上限。
        jitter_ratio: jitter 范围比例，0.5 表示 [0, 0.5*delay] 内随机。

    Returns:
        延迟秒数：min(base * 2^(attempt-1), max_delay) + jitter
    """
    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2**exponent), max_delay)

    jitter = random.uniform(0, jitter_ratio * delay)
    return delay + jitter
