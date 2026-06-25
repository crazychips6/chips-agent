"""ContextCompressor / ContextEngine 测试"""
import pytest
from agent.context_engine import ContextEngine, NullContextEngine
from agent.context_compressor import (
    ContextCompressor,
    _content_len,
    _rough_tokens,
    _tool_summary,
    _PRUNED_PLACEHOLDER,
)


class TestNullEngine:
    def test_never_compresses(self):
        e = NullContextEngine()
        assert e.name == "null"
        assert e.should_compress() is False
        assert e.should_compress(99999) is False
        msgs = [{"role": "user", "content": "hi"}]
        assert e.compress(msgs) is msgs

    def test_update_from_response(self):
        e = NullContextEngine()
        e.update_from_response({"prompt_tokens": 100, "completion_tokens": 50})
        assert e.last_prompt_tokens == 100


class TestHelpers:
    def test_content_len_str(self):
        assert _content_len("hello") == 5

    def test_content_len_list(self):
        ct = [{"type": "text", "text": "hi"}, {"type": "text", "text": "there"}]
        assert _content_len(ct) == 7

    def test_content_len_empty(self):
        assert _content_len("") == 0
        assert _content_len([]) == 0

    def test_rough_tokens(self):
        msgs = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
        ]
        assert _rough_tokens(msgs) > 0

    def test_tool_summary_bash(self):
        result = _tool_summary("bash", '{"command": "npm test"}', "line1\nline2\nline3")
        assert "npm test" in result
        assert "3 lines" in result

    def test_tool_summary_read(self):
        result = _tool_summary("read", '{"path": "main.py"}', "some content here")
        assert "main.py" in result
        assert "read" in result

    def test_tool_summary_unknown(self):
        result = _tool_summary("unknown_tool", '{"a": "1"}', "output")
        assert "unknown_tool" in result


class TestContextCompressor:
    def test_defaults(self):
        cc = ContextCompressor()
        assert cc.name == "compressor"
        assert cc.should_compress() is False  # no token data yet

    def test_should_compress(self):
        cc = ContextCompressor()
        cc.update_context_length(128_000)
        assert cc.threshold_tokens == 64_000
        assert cc.should_compress(70_000) is True
        assert cc.should_compress(10_000) is False

    def test_should_compress_no_threshold(self):
        cc = ContextCompressor()
        assert cc.should_compress(50000) is False  # threshold_tokens == 0

    def test_prune_tool_results(self):
        cc = ContextCompressor(protect_first_n=0)  # 不保护头部，使裁剪生效
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "read", "arguments": '{"path": "f.py"}'}}], "content": ""},
            {"role": "tool", "tool_call_id": "call_1", "content": "x" * 500},
        ]
        pruned, count = cc._prune_tool_results(msgs)
        assert count == 1
        assert len(pruned[2]["content"]) < 200  # 被替换为摘要

    def test_prune_skips_short(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "tool", "tool_call_id": "c1", "content": "short"},
        ]
        pruned, count = cc._prune_tool_results(msgs)
        assert count == 0  # 少于 200 字符不裁剪

    def test_align_boundary_forward(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "tool", "content": "skip"},
            {"role": "user", "content": "keep"},
        ]
        assert cc._align_boundary_forward(msgs, 0) == 1

    def test_align_boundary_backward(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}], "content": ""},
            {"role": "tool", "content": "result"},
            {"role": "user", "content": "next"},
        ]
        # 从 index 2 回退到 0（tool_call 组起点）
        assert cc._align_boundary_backward(msgs, 2) == 0

    def test_find_tail_cut(self):
        cc = ContextCompressor()
        cc.tail_token_budget = 500
        msgs = [{"role": "user", "content": "x" * 200} for _ in range(10)]
        cut = cc._find_tail_cut(msgs, 3)
        assert 3 <= cut <= 8

    def test_sanitize_removes_orphan(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "orphan", "content": "should be removed"},
        ]
        result = cc._sanitize_tool_pairs(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_sanitize_adds_stub(self):
        cc = ContextCompressor()
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "c1"}], "content": ""},
        ]
        result = cc._sanitize_tool_pairs(msgs)
        assert len(result) == 2
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "c1"

    def test_compress_skips_short(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        result = cc.compress(msgs)
        assert result == msgs  # 消息太少，不压缩

    def test_compress_with_summarize(self):
        """有 summarize_fn 时压缩应生成摘要。"""
        summary_text = "## Active Task\nNone\n## Completed Actions\nNothing"

        def mock_summarize(prompt: str) -> str:
            return summary_text

        cc = ContextCompressor(summarize_fn=mock_summarize)
        cc.update_context_length(128_000)
        # 制造超出阈值的 token 计数
        cc.last_prompt_tokens = 70000

        msgs = (
            [{"role": "user", "content": "first"}]
            + [{"role": "user", "content": "middle turn " + str(i)} for i in range(8)]
            + [{"role": "assistant", "content": "final"}]
        )

        result = cc.compress(msgs)
        # 结果中有摘要
        contents = [m.get("content", "") for m in result]
        assert any("COMPACTION" in c for c in contents), "摘要应包含 COMPACTION 前缀"
        assert len(result) < len(msgs), "压缩后消息数应减少"

    def test_compress_fallback_without_summarize(self):
        """无 summarize_fn 时，摘要失败但不应崩溃。"""
        cc = ContextCompressor(summarize_fn=None)
        cc.update_context_length(128_000)
        cc.last_prompt_tokens = 70000

        msgs = (
            [{"role": "user", "content": "first"}]
            + [{"role": "user", "content": "middle " + str(i)} for i in range(8)]
            + [{"role": "assistant", "content": "final"}]
        )
        # 不应抛出异常
        result = cc.compress(msgs)
        assert len(result) > 0

    def test_compress_updates_count(self):
        cc = ContextCompressor(summarize_fn=lambda p: "summary")
        cc.update_context_length(128_000)
        cc.last_prompt_tokens = 70000
        msgs = [{"role": "user", "content": str(i)} for i in range(20)]
        cc.compress(msgs)
        assert cc.compression_count == 1


class TestSanitizeNoCrash:
    def test_empty_messages(self):
        cc = ContextCompressor()
        assert cc._sanitize_tool_pairs([]) == []

    def test_no_tool_calls(self):
        cc = ContextCompressor()
        msgs = [{"role": "user", "content": "hi"}]
        assert cc._sanitize_tool_pairs(msgs) == msgs
