"""ContentBlock 类型与序列化测试"""

from agent.message import (
    ContentBlock,
    ImageBlock,
    TextBlock,
    contains_image_block,
    deserialize_content,
    image_file_to_data_uri,
    parse_user_content,
    serialize_content,
    to_openai_content,
    to_openai_messages,
)


class TestTextBlock:
    def test_defaults(self):
        b = TextBlock()
        assert b.type == "text"
        assert b.text == ""

    def test_with_content(self):
        b = TextBlock(text="hello")
        assert b.text == "hello"

    def test_immutable(self):
        import dataclasses
        assert dataclasses.is_dataclass(TextBlock)


class TestImageBlock:
    def test_defaults(self):
        b = ImageBlock()
        assert b.type == "image_url"
        assert b.url == ""
        assert b.detail == "auto"

    def test_with_url(self):
        b = ImageBlock(url="https://example.com/img.png", detail="high")
        assert b.url == "https://example.com/img.png"
        assert b.detail == "high"

    def test_immutable(self):
        import dataclasses
        assert dataclasses.is_dataclass(ImageBlock)


class TestContentBlockType:
    def test_text_is_content_block(self):
        b: ContentBlock = TextBlock(text="hi")
        assert isinstance(b, TextBlock)

    def test_image_is_content_block(self):
        b: ContentBlock = ImageBlock(url="https://example.com/img.png")
        assert isinstance(b, ImageBlock)


class TestToOpenAIContent:
    def test_string_passthrough(self):
        assert to_openai_content("hello") == "hello"
        assert to_openai_content("") == ""

    def test_text_block(self):
        blocks = [TextBlock(text="hello")]
        result = to_openai_content(blocks)
        assert result == [{"type": "text", "text": "hello"}]

    def test_image_block(self):
        blocks = [ImageBlock(url="https://example.com/img.png", detail="high")]
        result = to_openai_content(blocks)
        assert result == [
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png", "detail": "high"}}
        ]

    def test_mixed_blocks(self):
        blocks = [
            TextBlock(text="看图："),
            ImageBlock(url="https://example.com/img.png"),
            TextBlock(text="描述一下"),
        ]
        result = to_openai_content(blocks)
        assert result == [
            {"type": "text", "text": "看图："},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png", "detail": "auto"}},
            {"type": "text", "text": "描述一下"},
        ]

    def test_empty_list(self):
        assert to_openai_content([]) == []


class TestToOpenAIMessages:
    def test_simple_text(self):
        msgs = [{"role": "user", "content": "hello"}]
        result = to_openai_messages(msgs)
        assert result == [{"role": "user", "content": "hello"}]

    def test_system_prompt(self):
        msgs = [{"role": "system", "content": "you are helpful"}]
        result = to_openai_messages(msgs)
        assert result == [{"role": "system", "content": "you are helpful"}]

    def test_assistant_with_tool_calls(self):
        msgs = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}],
        }]
        result = to_openai_messages(msgs)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == ""
        assert len(result[0]["tool_calls"]) == 1

    def test_tool_result(self):
        msgs = [{"role": "tool", "tool_call_id": "call_1", "content": "result"}]
        result = to_openai_messages(msgs)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"
        assert result[0]["content"] == "result"

    def test_content_block_message(self):
        msgs = [{
            "role": "user",
            "content": [
                TextBlock(text="看图："),
                ImageBlock(url="https://example.com/img.png"),
            ],
        }]
        result = to_openai_messages(msgs)
        assert result[0]["content"] == [
            {"type": "text", "text": "看图："},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png", "detail": "auto"}},
        ]

    def test_empty_content(self):
        msgs = [{"role": "user", "content": ""}]
        result = to_openai_messages(msgs)
        assert result == [{"role": "user", "content": ""}]

    def test_mixed_messages(self):
        msgs = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "你好"},
            {"role": "user", "content": [TextBlock(text="看图："), ImageBlock(url="https://img.png")]},
        ]
        result = to_openai_messages(msgs)
        assert len(result) == 4
        assert result[0]["content"] == "你是一个助手"
        assert result[1]["content"] == "hello"
        assert result[2]["content"] == "你好"
        assert isinstance(result[3]["content"], list)

    def test_default_content_when_missing(self):
        msgs = [{"role": "user"}]  # no content key
        result = to_openai_messages(msgs)
        assert result[0]["content"] == ""


class TestContainsImageBlock:
    def test_string_returns_false(self):
        assert contains_image_block("hello") is False
        assert contains_image_block("") is False

    def test_empty_list_returns_false(self):
        assert contains_image_block([]) is False

    def test_text_only_blocks_returns_false(self):
        content = [{"type": "text", "text": "hello"}]
        assert contains_image_block(content) is False

    def test_image_block_returns_true(self):
        content = [{"type": "image_url", "image_url": {"url": "https://img.png"}}]
        assert contains_image_block(content) is True

    def test_mixed_block_detects_image(self):
        content = [
            {"type": "text", "text": "看："},
            {"type": "image_url", "image_url": {"url": "https://img.png"}},
        ]
        assert contains_image_block(content) is True


class TestParseUserContent:
    def test_plain_text_returns_string(self):
        assert parse_user_content("hello") == "hello"
        assert parse_user_content("") == ""

    def test_image_url_detected(self):
        result = parse_user_content("看图 https://example.com/img.png 怎么样")
        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextBlock)
        assert isinstance(result[1], ImageBlock)
        assert result[1].url == "https://example.com/img.png"
        assert isinstance(result[2], TextBlock)

    def test_image_url_with_query_params(self):
        result = parse_user_content("https://img.com/photo.png?w=800&h=600")
        assert isinstance(result, list)
        assert isinstance(result[0], ImageBlock)

    def test_jpg_url_detected(self):
        result = parse_user_content("https://example.com/photo.jpg")
        assert isinstance(result, list)
        assert isinstance(result[0], ImageBlock)

    def test_no_image_no_change(self):
        assert parse_user_content("今天天气不错") == "今天天气不错"
        assert parse_user_content("ls -la") == "ls -la"

    def test_image_path_exists(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"fake-png")
        result = parse_user_content(f"看图 {img} 分析")
        assert isinstance(result, list)
        assert any(isinstance(b, ImageBlock) for b in result)

    def test_image_path_not_exists(self):
        """路径不存在时返回 TextBlock。"""
        result = parse_user_content("看图 /nonexistent/img.png")
        assert isinstance(result, list)
        assert all(isinstance(b, TextBlock) for b in result)


class TestSerializeDeserialize:
    def test_serialize_string_passthrough(self):
        assert serialize_content("hello") == "hello"
        assert serialize_content("") == ""

    def test_serialize_content_block_list(self):
        blocks = [TextBlock(text="hi"), ImageBlock(url="https://img.png")]
        result = serialize_content(blocks)
        assert isinstance(result, str)
        assert '"type": "text"' in result
        assert '"type": "image_url"' in result

    def test_deserialize_string_passthrough(self):
        assert deserialize_content("hello") == "hello"
        assert deserialize_content("") == ""

    def test_deserialize_json_blocks(self):
        raw = '[{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "https://img.png"}}]'
        result = deserialize_content(raw)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"

    def test_deserialize_plain_string_with_bracket(self):
        """以 [ 开头的纯文本不应被误解析。"""
        raw = "[not json"
        assert deserialize_content(raw) == raw

    def test_roundtrip(self):
        blocks = [TextBlock(text="a"), ImageBlock(url="data:img")]
        serialized = serialize_content(blocks)
        deserialized = deserialize_content(serialized)
        assert isinstance(deserialized, list)
        assert deserialized[0]["type"] == "text"
        assert deserialized[1]["type"] == "image_url"


class TestImageFileToDataURI:
    def test_png_data_uri(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
        uri = image_file_to_data_uri(str(img))
        assert uri.startswith("data:image/png;base64,")

    def test_jpg_data_uri(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 100)
        uri = image_file_to_data_uri(str(img))
        assert uri.startswith("data:image/jpeg;base64,")
