"""OpenAIProvider — 通过 OpenAI SDK 调用兼容 API（DeepSeek / OpenAI / 等）

迁移自 agent/loop.py 的 _call_llm / _call_llm_streaming / _call_with_retry。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import openai
from openai import OpenAI

from agent.retry import jittered_backoff
from gateway.protocol import ModelGateway
from gateway.types import ChatResult

logger = logging.getLogger("chips.gateway.openai")


class OpenAIProvider(ModelGateway):
    """通过 OpenAI SDK 调用兼容 API。

    支持 retry（指数退避 + jitter）、streaming、usage 统计。
    """

    def __init__(self, api_key: str, base_url: str = "",
                 max_retries: int = 3):
        self.client = OpenAI(api_key=api_key, base_url=base_url or None)
        self._max_retries = max_retries

    # ── 非流式 ──

    def chat(self, messages: list[dict[str, Any]], model: str = "",
             **kwargs: Any) -> ChatResult:
        _res: dict[str, Any] = {}

        def _do_call():
            response = self.client.chat.completions.create(
                model=model, messages=messages, **kwargs,
            )
            _res["response"] = response
            return response.choices[0].message

        t0 = time.time()
        msg = _call_with_retry(_do_call, self._max_retries)
        elapsed = int((time.time() - t0) * 1000)

        result = ChatResult(
            content=msg.content or "",
            reasoning_content=getattr(msg, "reasoning_content", None),
            model=model,
            latency_ms=elapsed,
        )
        if msg.tool_calls:
            result.tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        if "response" in _res:
            usage = _res["response"].usage
            if usage:
                result.usage = {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                }

        logger.info("llm_call model=%s stream=false duration_ms=%d prompt_tokens=%d completion_tokens=%d",
                     model, elapsed,
                     result.usage.get("prompt_tokens", -1) if result.usage else -1,
                     result.usage.get("completion_tokens", -1) if result.usage else -1)
        return result

    # ── 流式 ──

    def chat_stream(self, messages: list[dict[str, Any]], model: str = "",
                    *, on_chunk: Callable[[str], None] | None = None,
                    **kwargs: Any) -> ChatResult:
        stream_kwargs = {**kwargs, "stream": True,
                         "stream_options": {"include_usage": True}}
        _usage: dict[str, int] = {}

        def _do_stream():
            stream = self.client.chat.completions.create(
                model=model, messages=messages, **stream_kwargs,
            )
            content = ""
            tool_calls: dict[int, dict[str, Any]] = {}

            for chunk in stream:
                if chunk.usage:
                    _usage["prompt"] = chunk.usage.prompt_tokens or 0
                    _usage["completion"] = chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if not delta:
                    continue

                if delta.content:
                    if on_chunk:
                        on_chunk(delta.content)
                    content += delta.content

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls:
                            tool_calls[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                        if tc.id:
                            tool_calls[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls[idx]["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls[idx]["function"]["arguments"] += tc.function.arguments

            result = ChatResult(content=content, model=model)
            if tool_calls:
                calls = []
                for i in sorted(tool_calls.keys()):
                    tc = tool_calls[i]
                    calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    })
                result.tool_calls = calls
            if _usage:
                result.usage = dict(_usage)
            return result

        t0 = time.time()
        msg = _call_with_retry(_do_stream, self._max_retries, desc="流式 LLM 调用")
        elapsed = int((time.time() - t0) * 1000)
        msg.latency_ms = elapsed

        logger.info("llm_call model=%s stream=true duration_ms=%d prompt_tokens=%d completion_tokens=%d",
                     model, elapsed,
                     msg.usage.get("prompt_tokens", -1) if msg.usage else -1,
                     msg.usage.get("completion_tokens", -1) if msg.usage else -1)
        return msg


# ── 模块级重试函数（独立可测） ──

def _call_with_retry(fn, max_retries: int, desc: str = "LLM 调用") -> Any:
    """调用 fn，遇可重试异常时退避重试。"""
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except openai.BadRequestError as e:
            raise RuntimeError(f"请求参数错误（不重试）：{e}")
        except openai.RateLimitError:
            last_error = "API 速率限制"
            if attempt < max_retries:
                delay = jittered_backoff(attempt)
                _log_retry(attempt, max_retries, last_error, delay)
                time.sleep(delay)
        except openai.APIStatusError as e:
            if e.status_code in (502, 503, 504):
                last_error = f"服务暂时不可用 ({e.status_code})"
                if attempt < max_retries:
                    delay = jittered_backoff(attempt, base_delay=2.0)
                    _log_retry(attempt, max_retries, last_error, delay)
                    time.sleep(delay)
            else:
                raise RuntimeError(f"API 错误 (HTTP {e.status_code}，不重试)：{e}")
        except openai.APITimeoutError:
            last_error = "请求超时"
            if attempt < max_retries:
                delay = jittered_backoff(attempt, base_delay=2.0)
                _log_retry(attempt, max_retries, last_error, delay)
                time.sleep(delay)
        except openai.APIConnectionError:
            last_error = "网络连接异常"
            if attempt < max_retries:
                delay = jittered_backoff(attempt, base_delay=2.0)
                _log_retry(attempt, max_retries, last_error, delay)
                time.sleep(delay)
        except openai.BadRequestError as e:
            raise RuntimeError(f"请求参数错误（不重试）：{e}")
        except Exception as e:
            last_error = f"未知错误：{e}"
            if attempt < max_retries:
                delay = jittered_backoff(attempt, base_delay=1.0)
                _log_retry(attempt, max_retries, last_error, delay)
                time.sleep(delay)
    raise RuntimeError(f"{desc}失败（已重试 {max_retries} 次）：{last_error}")


def _log_retry(attempt: int, max_retries: int, reason: str, delay: float):
    import sys
    logger.warning("llm_call retry attempt=%d/%d reason=%s", attempt, max_retries, reason)
    print(f"\n  [重试 {attempt}/{max_retries}] {reason}，等待 {delay:.0f}s...", file=sys.stderr)
