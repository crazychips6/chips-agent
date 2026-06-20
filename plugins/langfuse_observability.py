"""Langfuse 可观测性插件 — 全功能版

覆盖：
  1. trace/span 树（root → generation → tool）
  2. cost_details 费用分解（input/output/cache_read/cache_write）
  3. cache/reasoning token 追踪
  4. 确定性 trace_id（session_id + 时间戳）
  5. trace_id 贯穿日志
  6. 多会话并发隔离
  7. 全面异常保护

安装：
  uv sync --extra observability
  cp plugins/langfuse_observability.py ~/.chips/plugins/
  设置 LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY
"""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from typing import Any

logger = logging.getLogger("chips.plugins.langfuse")

_MAX_CHARS = 8000
_LANGFUSE_HOST = "https://cloud.langfuse.com"

# 模型定价（$ per 1K tokens），含 cache/reasoning 细分
_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat":           {"input": 0.00014, "output": 0.00028},
    "deepseek-reasoner":       {"input": 0.00055, "output": 0.00219, "cache_read": 0.00014, "reasoning": 0.00219},
    "gpt-4o":                  {"input": 0.0025,  "output": 0.01,    "cache_read": 0.00125},
    "gpt-4o-mini":             {"input": 0.00015, "output": 0.0006,  "cache_read": 0.000075},
    "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015, "cache_read": 0.0003, "cache_write": 0.00375},
    "gemini-1.5-pro":          {"input": 0.00125, "output": 0.005},
}


def _estimate_cost(model: str, prompt: int, completion: int,
                   cache_read: int = 0, cache_write: int = 0,
                   reasoning: int = 0) -> dict[str, float]:
    """估算费用分解，返回 {input, output, cache_read, cache_write, reasoning, total}。"""
    pricing = _PRICING.get(model, _PRICING.get("deepseek-chat", {}))
    result: dict[str, float] = {}
    result["input"] = round((prompt / 1000) * pricing.get("input", 0), 10)
    result["output"] = round((completion / 1000) * pricing.get("output", 0), 10)
    if cache_read:
        result["cache_read_input_tokens"] = round((cache_read / 1000) * pricing.get("cache_read", pricing.get("input", 0) * 0.5), 10)
    if cache_write:
        result["cache_creation_input_tokens"] = round((cache_write / 1000) * pricing.get("cache_write", pricing.get("input", 0)), 10)
    if reasoning:
        result["reasoning_tokens"] = round((reasoning / 1000) * pricing.get("reasoning", pricing.get("output", 0)), 10)
    result["total"] = round(sum(result.values()), 10)
    return result


def register(ctx):
    """PluginManager 注册入口。"""
    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("langfuse 未安装，跳过。uv sync --extra observability")
        return

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        logger.warning("LANGFUSE_PUBLIC_KEY 或 LANGFUSE_SECRET_KEY 未设置，跳过 Langfuse")
        return

    host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or _LANGFUSE_HOST
    client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    ctx.register_hook(LangfusePlugin(client))
    logger.info("langfuse_observability 已启用 host=%s", host)


class _TraceState:
    """单个 trace 的运行时状态。"""
    def __init__(self, trace_obs: Any, trace_id: str, session_id: str):
        self.trace_obs = trace_obs
        self.trace_id = trace_id
        self.session_id = session_id
        self.current_gen: Any = None
        self.current_tool: Any = None


class LangfusePlugin:
    """将 agent 执行数据发送到 Langfuse（SDK v4+）。

    线程安全：每个 session_id 独立 trace 状态，互不干扰。
    """

    def __init__(self, client):
        self._client = client
        self._lock = threading.Lock()
        # {session_id: _TraceState}
        self._states: dict[str, _TraceState] = {}

    def on_register(self, registry):
        pass

    # ── LLM 调用 ──

    def on_llm_call_pre(self, messages, model, kwargs):
        """LLM 调用前：创建/复用 trace，创建 generation。"""
        try:
            session_id = os.getenv("CHIPS_SESSION_ID", "unknown")
            with self._lock:
                state = self._states.get(session_id)

            # 如果已有未结束的 generation，先结束
            if state and state.current_gen is not None:
                try:
                    state.current_gen.end()
                except Exception:
                    pass
                state.current_gen = None

            # 没有 trace → 新建
            if state is None:
                trace_id = self._gen_trace_id(session_id)
                trace_obs = self._with_session(
                    session_id,
                    self._client.start_observation,
                    name="chips conversation",
                    as_type="trace",
                    input=self._safe(messages[-1:] if messages else ""),
                    metadata={"source": "chips", "session_id": session_id},
                )
                state = _TraceState(trace_obs, trace_id, session_id)
                with self._lock:
                    self._states[session_id] = state
                try:
                    self._client.flush()
                except Exception:
                    pass

            # 注入 trace_id 到日志
            from agent.logger import set_trace_id
            set_trace_id(state.trace_id)

            # 创建 generation（session_id 自动传播）
            state.current_gen = self._with_session(
                session_id,
                state.trace_obs.start_observation,
                name="LLM call",
                as_type="generation",
                input=self._safe(messages[-4:]),
                model=model,
                model_parameters={"max_tokens": kwargs.get("max_tokens", 4096)},
                metadata={"tool_count": len(kwargs.get("tools", []))},
            )
        except Exception:
            logger.exception("langfuse_on_llm_call_pre_error")
        return None

    def on_llm_call_post(self, messages, model, result, duration_ms):
        """LLM 调用后：结束 generation，写入 usage + cost_details。"""
        try:
            session_id = os.getenv("CHIPS_SESSION_ID", "unknown")
            with self._lock:
                state = self._states.get(session_id)
            if state is None or state.current_gen is None:
                return
            gen = state.current_gen
            state.current_gen = None

            usage = result.usage or {}

            # 提取各类型 token
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            # 提取缓存 + reasoning token（OpenAI SDK 格式）
            prompt_details = usage.get("prompt_tokens_details") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            cache_read = prompt_details.get("cached_tokens", 0)
            reasoning = completion_details.get("reasoning_tokens", 0)
            # Anthropic 格式兼容
            if not cache_read:
                cache_read = usage.get("cache_read_input_tokens", 0)
            if not reasoning:
                reasoning = usage.get("reasoning_tokens", 0)
            # cache_write 在某些 provider 中有
            cache_write = usage.get("cache_creation_input_tokens", 0)

            # 费用分解
            cost = _estimate_cost(model, prompt_tokens, completion_tokens,
                                  cache_read, cache_write, reasoning)

            # 构建 usage_details（Langfuse 标准字段名）
            usage_details = {
                "input": prompt_tokens,
                "output": completion_tokens,
            }
            if cache_read:
                usage_details["cache_read_input_tokens"] = cache_read
            if cache_write:
                usage_details["cache_creation_input_tokens"] = cache_write
            if reasoning:
                usage_details["reasoning_tokens"] = reasoning

            # 构建输出
            output = ""
            if result.tool_calls:
                tool_calls = []
                for tc in result.tool_calls:
                    fn = tc.get("function", {})
                    tool_calls.append({
                        "name": fn.get("name"),
                        "arguments": self._truncate(fn.get("arguments", "")),
                    })
                output = json.dumps({"tool_calls": tool_calls}, ensure_ascii=False)
            else:
                output = self._truncate(result.content or "")

            # SDK v4: end() 只接受 end_time，数据通过 update() 设置
            gen.update(
                output=output,
                usage_details=usage_details,
                cost_details=cost,
                metadata={"duration_ms": duration_ms},
            )
            gen.end()

            # flush 到 Langfuse，确保看板实时更新
            try:
                self._client.flush()
            except Exception:
                pass

            # 没有工具调用 → 关闭 trace
            if not result.tool_calls:
                self._finish_trace(session_id)
        except Exception:
            logger.exception("langfuse_on_llm_call_post_error")

    # ── 工具调用 ──

    def on_tool_call_pre(self, tool_name, args):
        """工具调用前：创建 tool observation。"""
        try:
            session_id = os.getenv("CHIPS_SESSION_ID", "unknown")
            with self._lock:
                state = self._states.get(session_id)
            if state is None:
                return None
            state.current_tool = self._with_session(
                session_id,
                state.trace_obs.start_observation,
                name=f"Tool: {tool_name}",
                as_type="tool",
                input=self._safe(args),
                metadata={"tool_name": tool_name},
            )
        except Exception:
            logger.exception("langfuse_on_tool_call_pre_error")
        return None

    def on_tool_call_post(self, tool_name, result):
        """工具调用后：结束 tool observation。"""
        try:
            session_id = os.getenv("CHIPS_SESSION_ID", "unknown")
            with self._lock:
                state = self._states.get(session_id)
            if state is None or state.current_tool is None:
                return None
            tool_obs = state.current_tool
            state.current_tool = None
            tool_obs.update(output=self._truncate(result))
            tool_obs.end()
            try:
                self._client.flush()
            except Exception:
                pass
        except Exception:
            logger.exception("langfuse_on_tool_call_post_error")
        return None

    # ── 会话结束 ──

    def on_response(self, response: str) -> str | None:
        return None

    def on_session_end(self, messages):
        """会话结束：关闭所有 trace 并 flush。"""
        try:
            with self._lock:
                session_ids = list(self._states.keys())
            for sid in session_ids:
                self._finish_trace(sid)
            self._client.flush()
        except Exception:
            logger.exception("langfuse_on_session_end_error")

    # ── 内部 ──

    @staticmethod
    def _with_session(session_id: str, fn, *args, **kwargs):
        """在 propagate_attributes 上下文中执行函数，自动关联 session_id。

        Langfuse SDK v4 通过 OTel context 传播 session_id，
        所有在此上下文中创建的 observation 自动归属于该 session。
        """
        from langfuse import propagate_attributes
        with propagate_attributes(session_id=session_id):
            return fn(*args, **kwargs)

    def _finish_trace(self, session_id: str):
        """结束指定 session 的 trace。"""
        try:
            with self._lock:
                state = self._states.pop(session_id, None)
            if state is None:
                return
            # 结束未关闭的 generation 或 tool
            if state.current_gen is not None:
                try:
                    state.current_gen.end()
                except Exception:
                    pass
            if state.current_tool is not None:
                try:
                    state.current_tool.end()
                except Exception:
                    pass
            if state.trace_obs is not None:
                try:
                    state.trace_obs.end()
                except Exception:
                    pass
            try:
                self._client.flush()
            except Exception:
                pass
            # 清除日志中的 trace_id
            from agent.logger import clear_trace_id
            clear_trace_id()
        except Exception:
            logger.exception("langfuse_finish_trace_error sid=%s", session_id)

    @staticmethod
    def _gen_trace_id(session_id: str) -> str:
        """生成确定性 trace_id：session_id + 时间戳。"""
        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        return f"{session_id}::{ts}"

    @staticmethod
    def _safe(value: Any, depth: int = 0) -> Any:
        """递归截断，防止序列化失败。"""
        if depth > 4:
            return "..."
        if isinstance(value, str):
            return value[:_MAX_CHARS]
        if isinstance(value, dict):
            return {k: LangfusePlugin._safe(v, depth + 1) for k, v in list(value.items())[:50]}
        if isinstance(value, list):
            return [LangfusePlugin._safe(v, depth + 1) for v in value[:50]]
        if isinstance(value, (int, float, bool, type(None))):
            return value
        try:
            s = str(value)
            return s[:_MAX_CHARS]
        except Exception:
            return "<unserializable>"

    @staticmethod
    def _truncate(text: str, max_len: int = _MAX_CHARS) -> str:
        if not isinstance(text, str):
            text = str(text)
        return text[:max_len] + "..." if len(text) > max_len else text
