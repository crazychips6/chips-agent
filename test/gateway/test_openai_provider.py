"""Gateway 单元测试 — OpenAIProvider + retry + streaming + rate_limit"""

from unittest.mock import MagicMock, patch

import openai
import pytest

from gateway.providers.openai import OpenAIProvider, _call_with_retry
from gateway.rate_limit import TokenBucket
from gateway.types import ChatResult


# ── ChatResult ──

class TestChatResult:
    def test_defaults(self):
        r = ChatResult()
        assert r.content == ""
        assert r.tool_calls is None
        assert r.reasoning_content is None
        assert r.usage is None

    def test_full(self):
        r = ChatResult(
            content="hi",
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}],
            reasoning_content="thinking",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            model="deepseek-chat",
            latency_ms=100,
        )
        assert r.content == "hi"
        assert r.tool_calls[0]["function"]["name"] == "echo"


# ── TokenBucket ──

class TestTokenBucket:
    def test_acquire_immediate(self):
        tb = TokenBucket(rate=10, capacity=10)
        assert tb.acquire(5) is True
        assert tb.acquire(5) is True
        assert tb.acquire(1) is False  # 桶已空

    def test_refill_over_time(self):
        tb = TokenBucket(rate=100, capacity=100)
        tb.acquire(100)  # 清空
        assert tb.acquire(1) is False
        # 模拟时间推进
        tb.last_refill -= 0.02  # 回退 20ms → 增加 2 个令牌
        assert tb.acquire(1) is True


# ── _call_with_retry ──

class TestCallWithRetry:
    def test_normal_call_succeeds(self):
        assert _call_with_retry(lambda: "ok", max_retries=3) == "ok"

    def test_rate_limit_retry_then_succeed(self):
        calls = []
        def _fn():
            calls.append(1)
            if len(calls) == 1:
                raise openai.RateLimitError("rate limited", response=MagicMock(), body=None)
            return "ok after retry"

        with patch("time.sleep"):  # 不实际等待
            result = _call_with_retry(_fn, max_retries=3)
        assert result == "ok after retry"
        assert len(calls) == 2

    def test_bad_request_not_retried(self):
        with pytest.raises(RuntimeError, match="请求参数错误"):
            _call_with_retry(
                lambda: (_ for _ in ()).throw(openai.BadRequestError("bad req", response=MagicMock(), body=None)),
                max_retries=3,
            )

    def test_exhaust_retries(self):
        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="失败"):
                _call_with_retry(
                    lambda: (_ for _ in ()).throw(openai.RateLimitError("always fail", response=MagicMock(), body=None)),
                    max_retries=2,
                )


# ── OpenAIProvider ──

class TestOpenAIProviderChat:
    @pytest.fixture
    def mock_sdk(self):
        with patch("gateway.providers.openai.OpenAI") as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    def test_chat_text(self, mock_sdk):
        msg = MagicMock()
        msg.content = "Hello"
        msg.reasoning_content = None
        msg.tool_calls = None
        mock_sdk.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)],
            usage=MagicMock(prompt_tokens=10, completion_tokens=20),
        )

        provider = OpenAIProvider(api_key="test-key", max_retries=1)
        result = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="deepseek-chat",
        )

        assert result.content == "Hello"
        assert result.tool_calls is None
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 20}
        assert result.model == "deepseek-chat"
        assert result.reasoning_content is None

    def test_chat_tool_calls(self, mock_sdk):
        tc = MagicMock()
        tc.id = "call_1"
        tc.type = "function"
        tc.function.name = "echo"
        tc.function.arguments = '{"text":"hi"}'

        msg = MagicMock()
        msg.content = None
        msg.reasoning_content = None
        msg.tool_calls = [tc]
        mock_sdk.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)],
            usage=MagicMock(prompt_tokens=5, completion_tokens=5),
        )

        provider = OpenAIProvider(api_key="test-key")
        result = provider.chat(messages=[], model="test")

        assert result.content == ""
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "echo"
        assert result.tool_calls[0]["function"]["arguments"] == '{"text":"hi"}'


class TestOpenAIProviderStream:
    @pytest.fixture
    def mock_sdk(self):
        with patch("gateway.providers.openai.OpenAI") as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    def test_stream_accumulates_content(self, mock_sdk):
        """流式调用正确累积分块内容。"""
        chunks = []
        for text in ["Hello", " ", "World", "!"]:
            chunk = MagicMock()
            choice = MagicMock()
            delta = MagicMock()
            delta.content = text
            delta.tool_calls = None
            choice.delta = delta
            choice.finish_reason = None
            chunk.choices = [choice]
            chunk.usage = None
            chunks.append(chunk)

        mock_sdk.chat.completions.create.return_value = chunks

        provider = OpenAIProvider(api_key="test-key", max_retries=1)
        collected = []

        result = provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="test",
            on_chunk=lambda c: collected.append(c),
        )

        assert result.content == "Hello World!"
        assert result.tool_calls is None
        assert "".join(collected) == "Hello World!"

    def test_stream_accumulates_tool_calls(self, mock_sdk):
        """流式调用正确累积分块的 tool_calls。"""
        def _make_chunk(delta_content=None, tool_calls=None, finish_reason=None, usage=None):
            chunk = MagicMock()
            choice = MagicMock()
            choice.delta = MagicMock()
            choice.delta.content = delta_content
            choice.delta.tool_calls = tool_calls
            choice.finish_reason = finish_reason
            chunk.choices = [choice]
            chunk.usage = usage
            return chunk

        tc2 = MagicMock()
        tc2.index = 0
        tc2.id = "call_1"
        tc2.function.name = "echo"
        tc2.function.arguments = ""

        tc3 = MagicMock()
        tc3.index = 0
        tc3.id = ""
        tc3.function.name = ""
        tc3.function.arguments = '{"text":"hello"}'

        c1 = _make_chunk(delta_content=None, tool_calls=None)
        c2 = _make_chunk(delta_content=None, tool_calls=[tc2])
        c3 = _make_chunk(delta_content=None, tool_calls=[tc3], finish_reason="tool_calls")

        mock_sdk.chat.completions.create.return_value = [c1, c2, c3]

        provider = OpenAIProvider(api_key="test-key", max_retries=1)
        result = provider.chat_stream(messages=[], model="test")

        assert result.content == ""
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "call_1"
        assert result.tool_calls[0]["function"]["name"] == "echo"
        assert result.tool_calls[0]["function"]["arguments"] == '{"text":"hello"}'
