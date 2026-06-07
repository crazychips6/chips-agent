"""Gateway 用量统计测试 — UsageRecorder"""

from unittest.mock import MagicMock

from gateway.stats import UsageRecorder
from gateway.types import ChatResult


class TestUsageRecorder:
    def test_records_chat_call(self):
        """非流式调用正确记录用量。"""
        inner = MagicMock()
        inner.chat.return_value = ChatResult(
            content="hi",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
        )

        recorder = UsageRecorder(inner)
        result = recorder.chat([{"role": "user", "content": "hi"}], model="deepseek-chat")

        assert result.content == "hi"
        assert recorder.call_count == 1
        assert recorder.total_prompt_tokens == 10
        assert recorder.total_completion_tokens == 20

    def test_records_stream_call(self):
        """流式调用正确记录用量。"""
        inner = MagicMock()
        inner.chat_stream.return_value = ChatResult(
            content="hello",
            usage={"prompt_tokens": 5, "completion_tokens": 15},
        )

        recorder = UsageRecorder(inner)
        result = recorder.chat_stream([], model="deepseek-chat")

        assert result.content == "hello"
        assert recorder.call_count == 1
        assert recorder.total_prompt_tokens == 5

    def test_summary_aggregates(self):
        """多轮调用后 summary 正确聚合。"""
        inner = MagicMock()

        def chat_side(messages, model="", **kw):
            return ChatResult(
                content="ok",
                usage={"prompt_tokens": 100, "completion_tokens": 50},
            )

        inner.chat = chat_side
        recorder = UsageRecorder(inner)

        recorder.chat([], model="deepseek-chat")
        recorder.chat([], model="deepseek-chat")

        s = recorder.summary()
        assert s["call_count"] == 2
        assert s["total_prompt_tokens"] == 200
        assert s["total_completion_tokens"] == 100
        assert s["total_tokens"] == 300

    def test_cost_estimation(self):
        """费用估算基于 pricing 表。"""
        inner = MagicMock()
        inner.chat.return_value = ChatResult(
            content="ok",
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
        )

        recorder = UsageRecorder(inner)
        recorder.chat([], model="deepseek-chat")

        # deepseek-chat: $0.00014/1K input, $0.00028/1K output
        # 1000 input = $0.00014, 500 output = $0.00014
        assert recorder.total_cost == 0.00028

    def test_unknown_model_no_cost(self):
        """未配置定价的模型费用为 0。"""
        inner = MagicMock()
        inner.chat.return_value = ChatResult(
            content="ok",
            usage={"prompt_tokens": 1000, "completion_tokens": 1000},
        )

        recorder = UsageRecorder(inner)
        recorder.chat([], model="unknown-model")
        assert recorder.total_cost == 0.0

    def test_no_usage_graceful(self):
        """usage 为 None 时不崩溃。"""
        inner = MagicMock()
        inner.chat.return_value = ChatResult(content="ok", usage=None)

        recorder = UsageRecorder(inner)
        recorder.chat([], model="deepseek-chat")
        assert recorder.call_count == 1
        assert recorder.total_prompt_tokens == 0

    def test_custom_pricing(self):
        """支持注入自定义定价覆盖默认。"""
        inner = MagicMock()
        inner.chat.return_value = ChatResult(
            content="ok",
            usage={"prompt_tokens": 1, "completion_tokens": 0},
        )

        recorder = UsageRecorder(inner, pricing={
            "my-model": {"input": 1.0, "output": 0.0},
        })
        recorder.chat([], model="my-model")
        # 1 token / 1000 * $1.0 = $0.001
        assert recorder.total_cost == 0.001

    def test_format_summary_empty(self):
        """无调用时 format_summary 返回空字符串。"""
        inner = MagicMock()
        recorder = UsageRecorder(inner)
        assert recorder.format_summary() == ""

    def test_format_summary(self):
        """有调用时 format_summary 返回格式化文本。"""
        inner = MagicMock()
        inner.chat.return_value = ChatResult(
            content="ok",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        recorder = UsageRecorder(inner)
        recorder.chat([], model="deepseek-chat")

        text = recorder.format_summary()
        assert "LLM 调用" in text
        assert "150" in text  # total tokens

    def test_reset_clears_stats(self):
        """reset() 清空所有统计。"""
        inner = MagicMock()
        inner.chat.return_value = ChatResult(
            content="ok",
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )

        recorder = UsageRecorder(inner)
        recorder.chat([], model="deepseek-chat")
        assert recorder.call_count == 1

        recorder.reset()
        assert recorder.call_count == 0
        assert recorder.total_cost == 0.0
