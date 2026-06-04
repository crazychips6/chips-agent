"""ToolRegistry — 工具注册与派发

使用 generation 计数器 + RLock 保证并发安全：任何 register/deregister 都会递增
generation，get_definitions 和 dispatch 在持有锁期间读到的是全一致的快照。

核心原则：
  - tool/ 模块零依赖其他模块
  - 工具通过 registry.register() 自注册，agent 只通过 dispatch() 调用
"""

import asyncio
import json
import logging
import time
from threading import RLock
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("chips")

# check_fn 结果的缓存秒数，避免每次 get_definitions 都重新执行昂贵的检查
_CHECK_FN_TTL = 30.0


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


class ToolRegistry:
    def __init__(self):
        self._entries: dict[str, ToolEntry] = {}
        # (时间戳, 结果) 缓存，由 _CHECK_FN_TTL 控制有效期
        self._check_fn_cache: dict[str, tuple[float, bool]] = {}
        self._lock = RLock()
        # 每次注册/注销递增，使外部可以检测到变更（后续供缓存失效使用）
        self._generation = 0

    def register(
        self,
        name: str,
        toolset: str = "",
        schema: Optional[dict] = None,
        handler: Optional[Callable] = None,
        check_fn: Optional[Callable[[], bool]] = None,
        is_async: bool = False,
        max_result_size_chars: int = 100_000,
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
            )
            self._generation += 1

    def deregister(self, name: str):
        with self._lock:
            self._entries.pop(name, None)
            self._check_fn_cache.pop(name, None)
            self._generation += 1

    def get_definitions(self, tool_names: set[str]) -> list[dict]:
        """返回指定工具的 OpenAI function-calling schema 列表。

        check_fn 返回 False 的工具会被过滤掉，结果有 30s 缓存。
        """
        result = []
        now = time.time()
        with self._lock:
            for name in tool_names:
                entry = self._entries.get(name)
                if not entry:
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
        """
        t0 = time.time()
        with self._lock:
            entry = self._entries.get(name)
        if not entry:
            logger.warning("tool=%s status=unknown_tool", name)
            return json.dumps({"error": f"unknown tool: {name}"})

        try:
            if entry.is_async:
                result = asyncio.run(entry.handler(args))
            else:
                result = entry.handler(args)
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            logger.warning("tool=%s status=error duration_ms=%d", name, elapsed)
            result = json.dumps({"error": f"{type(e).__name__}: {e}"})

        if isinstance(result, str):
            if len(result) > entry.max_result_size_chars:
                result = result[: entry.max_result_size_chars] + "\n...(truncated)"
        else:
            result = json.dumps(result, ensure_ascii=False)

        elapsed = int((time.time() - t0) * 1000)
        logger.info("tool=%s status=ok duration_ms=%d", name, elapsed)
        return result

    @property
    def tool_names(self) -> set[str]:
        """返回当前所有已注册的工具名称。"""
        with self._lock:
            return set(self._entries.keys())


# 模块级单例，供各工具模块 import 后直接调用 register()
registry = ToolRegistry()
