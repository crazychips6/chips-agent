"""token_counter 测试"""
import pytest
from agent.token_counter import (
    count_tokens,
    count_message_tokens,
    count_messages_tokens,
    set_encoding_for_model,
)


class TestCountTokens:
    def test_empty(self):
        assert count_tokens("") == 0
        assert count_tokens("", model="deepseek-chat") == 0

    def test_short_text(self):
        n = count_tokens("hello world")
        assert 1 < n < 10

    def test_chinese(self):
        n = count_tokens("你好世界")
        assert n > 0

    def test_long_text(self):
        n = count_tokens("word " * 1000)
        assert n > 100

    def test_with_model(self):
        n = count_tokens("hello world", model="deepseek-chat")
        assert n > 0

    def test_gpt4o_encoding(self):
        n = count_tokens("hello world", model="gpt-4o")
        assert n > 0


class TestCountMessageTokens:
    def test_simple_user_message(self):
        msg = {"role": "user", "content": "hello"}
        n = count_message_tokens(msg)
        assert n > 0

    def test_assistant_text(self):
        msg = {"role": "assistant", "content": "Here is my reply"}
        n = count_message_tokens(msg)
        assert n > 0

    def test_with_name(self):
        msg = {"role": "user", "content": "hi", "name": "alice"}
        n = count_message_tokens(msg)
        assert n > 0

    def test_with_tool_calls(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
                }
            ],
        }
        n = count_message_tokens(msg)
        assert n > 0

    def test_with_image_block(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "描述这张图"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            ],
        }
        n = count_message_tokens(msg)
        assert n > 0

    def test_empty_content(self):
        msg = {"role": "user", "content": ""}
        n = count_message_tokens(msg)
        assert n > 0  # 至少包含 role 和 overhead 开销


class TestCountMessagesTokens:
    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        n = count_messages_tokens(msgs)
        single = count_message_tokens(msgs[0]) + count_message_tokens(msgs[1])
        assert n == single

    def test_empty_list(self):
        assert count_messages_tokens([]) == 0

    def test_mixed_types(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "echo", "arguments": '{"x":1}'}},
            ]},
        ]
        n = count_messages_tokens(msgs)
        assert n > 0


class TestSetEncoding:
    def test_custom_mapping(self):
        set_encoding_for_model("my-custom-model", "cl100k_base")
        n = count_tokens("test", model="my-custom-model")
        assert n > 0
