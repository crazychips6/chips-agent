"""FallbackGateway — 模型不可用时自动降级到备用模型

装饰器模式，包装多个 ModelGateway 实例。
按优先级依次尝试，全部失败则抛出最后一个异常。

Usage::

    from gateway.fallback import FallbackGateway

    gateway = FallbackGateway([
        ("deepseek-chat",  OpenAIProvider(api_key=dk_key, base_url=dk_url)),
        ("gpt-4o-mini",    OpenAIProvider(api_key=oa_key, base_url=oa_url)),
        ("deepseek-reasoner", OpenAIProvider(api_key=dk_key, base_url=dk_url)),
    ])
    # FallbackGateway 实现了 ModelGateway 协议
    result = gateway.chat(messages=[...])  # 自动尝试列表中的 provider
"""

from __future__ import annotations

import logging
import time
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    APIStatusError,
    BadRequestError,
)

from gateway.protocol import ModelGateway
from gateway.types import ChatResult

logger = logging.getLogger("chips.gateway.fallback")


# 触发降级的异常类型（服务端问题 → 换备胎）
_FALLBACK_TRIGGERS = (
    APIConnectionError,   # 网络不通、DNS 解析失败、连接被拒绝
    APITimeoutError,      # 请求超时
    RateLimitError,       # 速率限制（说明当前 provider 过载）
)

# 服务不可用的 HTTP 状态码 → 触发降级
_FALLBACK_STATUS_CODES = {401, 429, 502, 503, 504}


class FallbackGateway(ModelGateway):
    """多模型降级网关。

    按 ``[(model_name, gateway), ...]`` 顺序依次尝试，
    前一个失败（服务端异常）则自动切换到下一个。
    全部失败时抛出最后的异常。
    """

    def __init__(
        self,
        backends: list[tuple[str, ModelGateway]],
    ):
        """
        Args:
            backends: 有序的 (模型名, Gateway 实例) 列表，
                      优先级从高到低排列。
        """
        if not backends:
            raise ValueError("至少需要提供一个后端模型")
        self._backends = backends
        self._last_failures: dict[str, dict[str, Any]] = {}

    @property
    def backend_count(self) -> int:
        return len(self._backends)

    @property
    def primary_model(self) -> str:
        """返回主用模型名。"""
        return self._backends[0][0]

    @property
    def fallback_models(self) -> list[str]:
        """返回所有备用模型名。"""
        return [m for m, _ in self._backends[1:]]

    def _should_fallback(self, exc: Exception) -> bool:
        """判断异常是否应该触发降级。

        服务端问题（网络不通、超时、限流、5xx） → 降级
        客户端问题（认证失败、参数错误） → 不降级（换了也不 work）
        """
        if isinstance(exc, _FALLBACK_TRIGGERS):
            return True
        if isinstance(exc, APIStatusError):
            return exc.status_code in _FALLBACK_STATUS_CODES
        # APIConnectionError 的子类（如 ConnectionError 封装）
        if isinstance(exc, ConnectionError):
            return True
        return False

    def _try_backends(self, method: str, messages: list[dict],
                      model: str = "", stream: bool = False,
                      on_chunk=None, **kwargs: Any) -> ChatResult:
        """依次尝试所有后端，直到成功或全部失败。"""
        errors: list[tuple[str, str, str]] = []  # (model, err_type, err_msg)

        for idx, (m, gw) in enumerate(self._backends):
            # 如果调用方指定了 model 且当前 backends 只有 1 个 →
            # 用调用方指定的 model（兼容 loop.py 传入自定义模型）
            actual_model = m if len(self._backends) > 1 else (model or m)

            try:
                if stream:
                    result = gw.chat_stream(
                        messages=messages, model=actual_model,
                        on_chunk=on_chunk, **kwargs,
                    )
                else:
                    result = gw.chat(
                        messages=messages, model=actual_model, **kwargs,
                    )
                # 成功后清除该后端的失败记录
                self._last_failures.pop(actual_model, None)
                # 如果有降级历史，打印恢复提示
                if errors:
                    import sys
                    print(f"\n  ✅ 模型恢复: {actual_model}（之前 {len(errors)} 个后端不可用）", file=sys.stderr)
                    logger.info(
                        "fallback_recovered model=%s after %d failures",
                        actual_model, len(errors),
                    )
                return result

            except BadRequestError:
                # 参数错误不重试也不降级（换模型也一样错）
                raise
            except Exception as exc:
                err_type = type(exc).__name__
                err_msg = str(exc)[:120]
                errors.append((actual_model, err_type, err_msg))
                logger.warning(
                    "fallback_attempt model=%s attempt=%d/%d error=%s",
                    actual_model, idx + 1, len(self._backends), err_msg,
                )
                self._last_failures[actual_model] = {
                    "error_type": err_type,
                    "error": err_msg,
                    "timestamp": time.time(),
                }

                # 最后一个也失败了 → 不再降级，抛出汇总异常
                if idx == len(self._backends) - 1:
                    import sys
                    _detail = "; ".join(f"{m} [{t}]" for m, t, _ in errors)
                    print(f"\n  ❌ 所有 {len(self._backends)} 个模型均不可用: {_detail}", file=sys.stderr)
                    detail = "; ".join(
                        f"{m}: [{t}] {e}" for m, t, e in errors
                    )
                    raise RuntimeError(
                        f"所有模型均不可用（已尝试 {len(self._backends)} 个）：{detail}"
                    ) from exc

                # 非致命异常（如超时）才继续降级
                if not self._should_fallback(exc):
                    import sys
                    print(f"\n  ⛔ {actual_model} 不可恢复错误（{err_type}），不降级", file=sys.stderr)
                    raise

                # 不是最后一个 → 打印降级提示，继续试下一个
                import sys
                next_model = self._backends[idx + 1][0]
                print(f"\n  ⚠ {actual_model} 不可用（{err_type}: {err_msg[:60]}）", file=sys.stderr)
                print(f"    → 切换到 {next_model}...", file=sys.stderr)
                continue

        # 理论上不会走到这里
        raise RuntimeError("fallback_gateway: 所有后端均失败")

    # ── ModelGateway 接口 ──

    def chat(self, messages: list[dict[str, Any]], model: str = "",
             **kwargs: Any) -> ChatResult:
        return self._try_backends(
            "chat", messages, model=model, stream=False, **kwargs,
        )

    def chat_stream(self, messages: list[dict[str, Any]], model: str = "",
                    *, on_chunk=None, **kwargs: Any) -> ChatResult:
        return self._try_backends(
            "chat_stream", messages, model=model, stream=True,
            on_chunk=on_chunk, **kwargs,
        )

    # ── 状态查询 ──

    @property
    def healthy(self) -> bool:
        """是否有至少一个后端最近没有失败。"""
        if not self._last_failures:
            return True
        # 如果主用模型最近没有失败 → 健康
        primary = self._backends[0][0]
        return primary not in self._last_failures

    def last_error(self, model: str | None = None) -> dict | None:
        """查询某模型（或主用模型）的最后一次失败详情。"""
        if model is None:
            model = self._backends[0][0]
        return self._last_failures.get(model)

    def summary(self) -> str:
        lines = [f"主用模型: {self._backends[0][0]}"]
        lines.append(f"备用模型: {', '.join(self.fallback_models) if self.fallback_models else '无'}")
        lines.append(f"健康状态: {'✅' if self.healthy else '⚠ 降级中'}")
        if self._last_failures:
            lines.append("近期降级记录:")
            for model, info in self._last_failures.items():
                lines.append(f"  {model}: [{info['error_type']}] {info['error'][:60]}")
        return "\n".join(lines)
