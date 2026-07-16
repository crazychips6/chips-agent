"""ToolRegistry — 工具注册与派发

使用 generation 计数器 + RLock 保证并发安全：任何 register/deregister 都会递增
generation，get_definitions 和 dispatch 在持有锁期间读到的是全一致的快照。

核心原则：
  - tool/ 模块零依赖其他模块
  - 工具通过 registry.register() 自注册，agent 只通过 dispatch() 调用

生产级特性：
  - 多级缓存：L1 (LRU) + L2 (TTL) 自动缓存幂等工具结果
  - 熔断器：防止工具持续失败导致雪崩
  - 可观测性：工具调用追踪和指标收集
"""

import asyncio
import json
import logging
import time
from threading import RLock
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("chips.tool.registry")

# check_fn 结果的缓存秒数，避免每次 get_definitions 都重新执行昂贵的检查
_CHECK_FN_TTL = 30.0

# 默认缓存和熔断器配置
_DEFAULT_CACHE_L1_SIZE = 100
_DEFAULT_CACHE_L2_TTL = 300.0
_DEFAULT_FAILURE_THRESHOLD = 5
_DEFAULT_RECOVERY_TIMEOUT = 60.0

# 默认不缓存的工具（有副作用）
_NO_CACHE_TOOLS = frozenset([
    "exec", "write", "bash", "shell", "run", "execute",
    "delete", "remove", "send", "publish",
])

# 默认不启用熔断器的工具
_NO_CIRCUIT_BREAKER_TOOLS = frozenset([
    "memory_search", "memory_add",  # 记忆工具总是可用
])


@dataclass
class ToolEntry:
    name: str
    toolset: str
    schema: dict
    handler: Callable
    # 可选的条件判定函数：返回 False 时该工具不会暴露给 LLM
    check_fn: Optional[Callable[[], bool]] = None
    is_async: bool = False
    # 工具返回结果超过此长度时截断，防止 token 溢出
    max_result_size_chars: int = 100_000
    # 工具分组：core（永远加载）| dev | agent | ...
    group: str = "core"
    # 模型可见范围：all（所有模型）| large（仅大模型）
    model_scope: str = "all"
    # 缓存配置：None 使用默认，True 启用，False 禁用
    enable_cache: Optional[bool] = None
    # 熔断器配置：None 使用默认，True 启用，False 禁用
    enable_circuit_breaker: Optional[bool] = None
    # 是否幂等（相同输入相同输出，可缓存）
    is_idempotent: Optional[bool] = None


class ToolRegistry:
    def __init__(
        self,
        enable_cache: bool = True,
        enable_circuit_breaker: bool = True,
        cache_l1_size: int = _DEFAULT_CACHE_L1_SIZE,
        cache_l2_ttl: float = _DEFAULT_CACHE_L2_TTL,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = _DEFAULT_RECOVERY_TIMEOUT,
    ):
        """
        Args:
            enable_cache: 是否启用工具结果缓存
            enable_circuit_breaker: 是否启用熔断器
            cache_l1_size: L1 缓存大小
            cache_l2_ttl: L2 缓存 TTL（秒）
            failure_threshold: 熔断器失败阈值
            recovery_timeout: 熔断器恢复超时（秒）
        """
        self._entries: dict[str, ToolEntry] = {}
        # (时间戳, 结果) 缓存，由 _CHECK_FN_TTL 控制有效期
        self._check_fn_cache: dict[str, tuple[float, bool]] = {}
        self._lock = RLock()
        # 每次注册/注销递增，使外部可以检测到变更（后续供缓存失效使用）
        self._generation = 0

        # 生产级特性
        self._enable_cache = enable_cache
        self._enable_circuit_breaker = enable_circuit_breaker

        # 缓存
        self._tool_cache = None
        if enable_cache:
            from utils.cache import ToolResultCache
            self._tool_cache = ToolResultCache(
                l1_size=cache_l1_size,
                l2_ttl=cache_l2_ttl,
            )

        # 熔断器
        self._circuit_breakers = None
        if enable_circuit_breaker:
            from utils.circuit_breaker import ToolCircuitBreakerManager
            self._circuit_breakers = ToolCircuitBreakerManager(
                default_failure_threshold=failure_threshold,
                default_recovery_timeout=recovery_timeout,
            )

        # 统计
        self._stats = {
            "total_dispatches": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "circuit_breaker_blocks": 0,
            "errors": 0,
        }

    def register(
        self,
        name: str,
        toolset: str = "",
        schema: Optional[dict] = None,
        handler: Optional[Callable] = None,
        check_fn: Optional[Callable[[], bool]] = None,
        is_async: bool = False,
        max_result_size_chars: int = 100_000,
        group: str = "core",
        model_scope: str = "all",
        enable_cache: Optional[bool] = None,
        enable_circuit_breaker: Optional[bool] = None,
        is_idempotent: Optional[bool] = None,
    ):
        with self._lock:
            self._entries[name] = ToolEntry(
                name=name,
                toolset=toolset,
                schema=schema or {"name": name},
                handler=handler or (lambda _: ""),
                check_fn=check_fn,
                is_async=is_async,
                max_result_size_chars=max_result_size_chars,
                group=group,
                model_scope=model_scope,
                enable_cache=enable_cache,
                enable_circuit_breaker=enable_circuit_breaker,
                is_idempotent=is_idempotent,
            )
            self._generation += 1

            # 注册时失效该工具的缓存
            if self._tool_cache and enable_cache is not False:
                self._tool_cache.invalidate(name)

    def deregister(self, name: str):
        with self._lock:
            self._entries.pop(name, None)
            self._check_fn_cache.pop(name, None)
            self._generation += 1

    def get_definitions(self, tool_names: set[str], *, model_scope: str | None = None) -> list[dict]:
        """返回指定工具的 OpenAI function-calling schema 列表。

        check_fn 返回 False 的工具会被过滤掉，结果有 30s 缓存。
        model_scope 非空时，只返回匹配该范围的工具（all 匹配任何 scope）。
        """
        result = []
        now = time.time()
        with self._lock:
            for name in tool_names:
                entry = self._entries.get(name)
                if not entry:
                    continue
                # 模型范围过滤
                if model_scope and entry.model_scope != "all" and entry.model_scope != model_scope:
                    continue
                if entry.check_fn:
                    cached = self._check_fn_cache.get(name)
                    if cached and (now - cached[0]) < _CHECK_FN_TTL:
                        ok = cached[1]
                    else:
                        ok = entry.check_fn()
                        self._check_fn_cache[name] = (now, ok)
                    if not ok:
                        continue
                result.append(entry.schema)
        return result

    def dispatch(self, name: str, args: dict) -> str:
        """派发工具调用，返回序列化结果字符串。

        支持同步/异步 handler；异常会被捕获并序列化为 JSON 错误返回（不会抛到上层）。

        生产级特性：
        - 缓存：幂等工具自动缓存结果
        - 熔断器：防止持续失败
        - 可观测性：记录调用统计
        """
        self._stats["total_dispatches"] += 1
        t0 = time.time()

        with self._lock:
            entry = self._entries.get(name)
        if not entry:
            logger.warning("tool=%s status=unknown_tool", name)
            return json.dumps({"error": f"unknown tool: {name}"})

        # 检查熔断器
        if self._circuit_breakers and self._should_use_circuit_breaker(entry):
            if not self._circuit_breakers.can_execute(name):
                self._stats["circuit_breaker_blocks"] += 1
                elapsed = int((time.time() - t0) * 1000)
                logger.warning("tool=%s status=circuit_broken duration_ms=%d", name, elapsed)
                return json.dumps({
                    "error": f"Tool '{name}' is temporarily unavailable (circuit breaker open). "
                             "Please try again later."
                })

        # 检查缓存
        if self._tool_cache and self._should_use_cache(entry):
            cached = self._tool_cache.get(name, args)
            if cached is not None:
                self._stats["cache_hits"] += 1
                elapsed = int((time.time() - t0) * 1000)
                logger.info("tool=%s status=cache_hit duration_ms=%d", name, elapsed)
                return cached
            self._stats["cache_misses"] += 1

        # 执行工具
        success = False
        try:
            if entry.is_async:
                result = asyncio.run(entry.handler(args))
            else:
                result = entry.handler(args)
            success = True
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            logger.warning("tool=%s status=error duration_ms=%d", name, elapsed)
            self._stats["errors"] += 1
            result = json.dumps({"error": f"{type(e).__name__}: {e}"})

            # 记录失败到熔断器
            if self._circuit_breakers and self._should_use_circuit_breaker(entry):
                self._circuit_breakers.record_failure(name)

        # 记录成功到熔断器
        if success and self._circuit_breakers and self._should_use_circuit_breaker(entry):
            self._circuit_breakers.record_success(name)

        # 处理结果
        if isinstance(result, str):
            if len(result) > entry.max_result_size_chars:
                result = result[: entry.max_result_size_chars] + "\n...(truncated)"
        else:
            result = json.dumps(result, ensure_ascii=False)

        # 缓存结果（仅成功且幂等的工具）
        if success and self._tool_cache and self._should_use_cache(entry):
            # 检查结果是否是错误
            if not result.startswith('{"error"'):
                self._tool_cache.set(name, args, result)

        elapsed = int((time.time() - t0) * 1000)
        logger.info("tool=%s status=ok duration_ms=%d", name, elapsed)
        return result

    def _should_use_cache(self, entry: ToolEntry) -> bool:
        """判断是否应该使用缓存"""
        if entry.enable_cache is False:
            return False
        if entry.enable_cache is True:
            return True
        # 默认：检查是否是幂等工具
        if entry.is_idempotent is True:
            return True
        if entry.is_idempotent is False:
            return False
        # 检查工具名是否在禁止缓存列表中
        return entry.name not in _NO_CACHE_TOOLS

    def _should_use_circuit_breaker(self, entry: ToolEntry) -> bool:
        """判断是否应该使用熔断器"""
        if entry.enable_circuit_breaker is False:
            return False
        if entry.enable_circuit_breaker is True:
            return True
        # 检查工具名是否在禁止熔断列表中
        return entry.name not in _NO_CIRCUIT_BREAKER_TOOLS

    def get_schema(self, name: str) -> dict | None:
        """返回单个工具的 schema 定义。"""
        entry = self._entries.get(name)
        if entry is None:
            return None
        return entry.schema

    # ── toolset 查询 ──

    def get_tool_names_for_toolset(self, toolset: str) -> list[str]:
        """返回指定 toolset 下的所有工具名。"""
        with self._lock:
            return sorted(
                name for name, e in self._entries.items()
                if e.toolset == toolset
            )

    def get_registered_toolset_names(self) -> list[str]:
        """返回所有出现过的 toolset 名。"""
        with self._lock:
            names: set[str] = set()
            for e in self._entries.values():
                if e.toolset:
                    names.add(e.toolset)
            return sorted(names)

    def get_toolset_for_tool(self, name: str) -> str | None:
        """返回指定工具所属的 toolset 名。"""
        with self._lock:
            entry = self._entries.get(name)
            return entry.toolset if entry else None

    def check_toolset_availability(self, toolset: str) -> bool:
        """检查一个 toolset 是否有可用工具（任一 check_fn 通过即可）。

        没有 check_fn 的工具视为永远可用。
        完全无工具返回 False。
        """
        now = time.time()
        with self._lock:
            entries = [
                e for e in self._entries.values()
                if e.toolset == toolset
            ]
        if not entries:
            return False
        for e in entries:
            if not e.check_fn:
                return True  # 有无 check_fn 的工具，直接可用
            cached = self._check_fn_cache.get(e.name)
            if cached and (now - cached[0]) < _CHECK_FN_TTL:
                if cached[1]:
                    return True
            else:
                ok = e.check_fn()
                self._check_fn_cache[e.name] = (now, ok)
                if ok:
                    return True
        return False

    @property
    def tool_names(self) -> set[str]:
        """返回当前所有已注册的工具名称。"""
        with self._lock:
            return set(self._entries.keys())

    # ── 生产级特性方法 ──

    def get_cache_stats(self) -> dict:
        """获取缓存统计"""
        if self._tool_cache:
            return self._tool_cache.get_stats()
        return {"enabled": False}

    def get_circuit_breaker_states(self) -> dict:
        """获取所有熔断器状态"""
        if self._circuit_breakers:
            return self._circuit_breakers.get_all_states()
        return {"enabled": False}

    def get_open_circuits(self) -> list:
        """获取所有熔断中的工具"""
        if self._circuit_breakers:
            return self._circuit_breakers.get_open_circuits()
        return []

    def reset_circuit_breaker(self, tool_name: Optional[str] = None):
        """重置熔断器"""
        if self._circuit_breakers:
            self._circuit_breakers.reset(tool_name)

    def invalidate_cache(self, tool_name: Optional[str] = None):
        """使缓存失效"""
        if self._tool_cache:
            self._tool_cache.invalidate(tool_name)

    def get_stats(self) -> dict:
        """获取注册表统计"""
        return {
            **self._stats,
            "tool_count": len(self._entries),
            "cache": self.get_cache_stats(),
            "circuit_breakers": {
                "enabled": self._circuit_breakers is not None,
                "open": self.get_open_circuits(),
            },
        }


# 模块级单例，供各工具模块 import 后直接调用 register()
registry = ToolRegistry()
