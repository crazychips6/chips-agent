"""UsageRecorder — ModelGateway 装饰器，记录每次 LLM 调用的用量和费用"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from gateway.protocol import ModelGateway
from gateway.types import ChatResult

# 默认模型定价（$ per 1K tokens）
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-reasoner": {"input": 0.00055, "output": 0.00219},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
}


class UsageRecorder(ModelGateway):
    """ModelGateway 装饰器。

    透明包装任意 ModelGateway，自动记录每次 LLM 调用的：
    - token 用量（prompt / completion）
    - 延迟
    - 预估费用
    - 时间戳

    Usage::

        gateway = UsageRecorder(OpenAIProvider(...))
        gateway._session_db = session_db   # 可选，持久化
        gateway._session_id = session_id
        agent.gateway = gateway

        # 会话结束后取统计
        print(gateway.summary())
    """

    def __init__(
        self,
        gateway: ModelGateway,
        pricing: dict[str, dict[str, float]] | None = None,
    ):
        self._inner = gateway
        self._pricing = {**DEFAULT_PRICING, **(pricing or {})}
        self._session_db: Any = None  # SessionDB | None，避免循环 import
        self._session_id: str = ""
        self.reset()

    def reset(self):
        """重置会话级统计。"""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    # ── ModelGateway ──

    def chat(self, messages: list[dict[str, Any]], model: str = "",
             **kwargs: Any) -> ChatResult:
        t0 = time.monotonic()
        result = self._inner.chat(messages, model=model, **kwargs)
        self._record(model, result.usage, int((time.monotonic() - t0) * 1000))
        return result

    def chat_stream(self, messages: list[dict[str, Any]], model: str = "",
                    *, on_chunk: Callable[[str], None] | None = None,
                    **kwargs: Any) -> ChatResult:
        t0 = time.monotonic()
        result = self._inner.chat_stream(messages, model=model,
                                         on_chunk=on_chunk, **kwargs)
        self._record(model, result.usage, int((time.monotonic() - t0) * 1000))
        return result

    # ── 内部 ──

    def _record(self, model: str, usage: dict[str, int] | None,
                latency_ms: int):
        prompt = (usage or {}).get("prompt_tokens", 0)
        completion = (usage or {}).get("completion_tokens", 0)
        cost = self._estimate_cost(model, prompt, completion)

        self.total_prompt_tokens += prompt
        self.total_completion_tokens += completion
        self.total_cost += cost
        self.call_count += 1

        record: dict[str, Any] = {
            "model": model,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "latency_ms": latency_ms,
            "cost": cost,
            "timestamp": time.time(),
        }
        self.calls.append(record)

        if self._session_db and self._session_id:
            try:
                self._session_db.insert_usage(
                    session_id=self._session_id,
                    model=model,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    latency_ms=latency_ms,
                    cost_estimate=cost,
                )
            except Exception:
                pass  # 持久化失败不影响主流程

    def _estimate_cost(self, model: str, prompt_tokens: int,
                       completion_tokens: int) -> float:
        pricing = self._pricing.get(model)
        if not pricing:
            return 0.0
        input_cost = (prompt_tokens / 1000) * pricing["input"]
        output_cost = (completion_tokens / 1000) * pricing["output"]
        return round(input_cost + output_cost, 6)

    # ── 统计 ──

    def summary(self) -> dict[str, Any]:
        """返回会话级的聚合统计。"""
        avg_latency = (
            round(sum(c["latency_ms"] for c in self.calls) / len(self.calls))
            if self.calls else 0
        )
        return {
            "call_count": self.call_count,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "total_cost": round(self.total_cost, 6),
            "avg_latency_ms": avg_latency,
        }

    def format_summary(self) -> str:
        """格式化的统计摘要文本。"""
        s = self.summary()
        if s["call_count"] == 0:
            return ""
        lines = [
            "── 会话统计 ──",
            f"LLM 调用: {s['call_count']} 次",
            f"Tokens:   {s['total_prompt_tokens']:,} 输入 + {s['total_completion_tokens']:,} 输出 = {s['total_tokens']:,}",
            f"费用:     ${s['total_cost']:.6f}",
            f"延迟:     {s['avg_latency_ms']}ms 平均",
        ]
        return "\n".join(lines)
