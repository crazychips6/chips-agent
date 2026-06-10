"""Agent Pool — 按角色的并发上限控制

每个 Agent 角色（researcher/coder）有独立的 Semaphore，
限制同一时间最多 N 个实例在运行。

用在 orchestrate 的 _run_single_step 中：
  acquire("researcher", pool_size=3)
  try:
      sub.run_conversation(...)
  finally:
      release("researcher")
"""

from __future__ import annotations

import logging
from threading import Semaphore

logger = logging.getLogger("chips.agent.pool")

_pools: dict[str, Semaphore] = {}
_default_pool_size = 5


def set_default_pool_size(size: int) -> None:
    """修改全局默认池大小。"""
    global _default_pool_size
    _default_pool_size = max(1, size)


def acquire(agent_name: str, pool_size: int | None = None) -> None:
    """进入角色池，无空闲位置时阻塞。"""
    size = pool_size or _default_pool_size
    if agent_name not in _pools:
        _pools[agent_name] = Semaphore(size)
        logger.debug("pool created name=%s size=%d", agent_name, size)
    _pools[agent_name].acquire()


def release(agent_name: str) -> None:
    """退出角色池。"""
    sem = _pools.get(agent_name)
    if sem is not None:
        sem.release()


def active_count(agent_name: str) -> int:
    """当前正在运行的实例数（近似）。"""
    sem = _pools.get(agent_name)
    if sem is None:
        return 0
    # Semaphore 没有直接的方法获取已用计数，
    # 用 _value 是内部实现，仅用于调试
    return max(0, (sem._value if hasattr(sem, "_value") else 0))  # noqa: SLF001


def reset() -> None:
    """清空所有池（测试用）。"""
    _pools.clear()
