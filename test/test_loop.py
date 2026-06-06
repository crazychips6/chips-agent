"""AIAgent 循环测试 — mock OpenAI 客户端，验证 prompt 组装和 ReAct 行为"""

from unittest.mock import MagicMock, patch

import openai
import pytest

from agent.loop import AIAgent
from agent.message import ImageBlock, TextBlock


@pytest.fixture
def mock_openai():
    with patch("agent.loop.OpenAI") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_openai_raw():
    """mock 整个 openai 模块，包括异常类。"""
    with patch("agent.loop.openai") as mock:
        yield mock


@pytest.fixture
def mock_openai():
    with patch("agent.loop.OpenAI") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def _make_msg(content: str | None, tool_calls=None, reasoning_content=None):
    """构造模拟的 LLM response message。"""
    msg = MagicMock()
    msg.content = content
    msg.reasoning_content = reasoning_content
    msg.tool_calls = tool_calls
    return msg


def _make_tool_call(id: str, name: str, args: str):
    tc = MagicMock()
    tc.id = id
    tc.type = "function"
    tc.function.name = name
    tc.function.arguments = args
    return tc


class TestBuildAssistantMsg:
    """_build_assistant_msg 将 API 返回转普通 dict。"""

    def test_content_only(self):
        agent = AIAgent(api_key="test-key")
        msg = _make_msg(content="你好")
        d = agent._build_assistant_msg(msg)
        assert d["role"] == "assistant"
        assert d["content"] == "你好"
        assert "reasoning_content" not in d
        assert "tool_calls" not in d

    def test_reasoning_content(self):
        """DeepSeek reasoning_content 字段被保留。"""
        agent = AIAgent(api_key="test-key")
        msg = _make_msg(content="最终回答", reasoning_content="思考过程")
        d = agent._build_assistant_msg(msg)
        assert d["reasoning_content"] == "思考过程"

    def test_no_reasoning(self):
        """没有 reasoning_content 时不添加该字段。"""
        agent = AIAgent(api_key="test-key")
        msg = _make_msg(content="回复")
        # 不设置 reasoning_content，getattr 回退到 None
        d = agent._build_assistant_msg(msg)
        assert "reasoning_content" not in d

    def test_tool_calls(self):
        agent = AIAgent(api_key="test-key")
        tc = _make_tool_call("call_1", "echo", '{"text":"hello"}')
        msg = _make_msg(content=None, tool_calls=[tc])
        d = agent._build_assistant_msg(msg)
        assert d["content"] == ""
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["function"]["name"] == "echo"


class TestRunConversation:
    """run_conversation 的 prompt 组装与 ReAct 流程。"""

    def test_prompt_has_all_layers(self, mock_openai):
        """有记忆/上下文/工具时，7 层全部出现在 system prompt 中。"""
        msg = _make_msg(content="回复")
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = [
            {"function": {"name": "echo", "description": "回显"}}
        ]
        agent.tool_names = {"echo"}
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = "项目记忆"
        agent.memory.get_user.return_value = "用户偏好"
        agent.memory.get_episodic.return_value = ""
        agent.context_files = [("/a", "CHIP.md", "# 项目说明")]

        agent.run_conversation("hi")

        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        for layer in ("核心身份", "当前日期", "用户偏好", "持久记忆",
                      "项目上下文", "工具规则", "调用约定"):
            assert f"# {layer}" in system, f"缺少层: {layer}"

    def test_prompt_layers_conditional(self, mock_openai):
        """无记忆、无上下文、无工具时，只有 3 个必现层。"""
        msg = _make_msg(content="ok")
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""
        agent.context_files = []

        agent.run_conversation("hi")

        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        assert "# 核心身份" in system
        assert "# 当前日期" in system
        assert "# 调用约定" in system
        assert "# 用户偏好" not in system
        assert "# 持久记忆" not in system
        assert "# 项目上下文" not in system
        assert "# 工具规则" not in system

    def test_memory_none_no_crash(self, mock_openai):
        """memory 为 None 时不崩溃。"""
        msg = _make_msg(content="ok")
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key")
        agent.memory = None
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

        reply = agent.run_conversation("hi")
        assert reply == "ok"

    def test_react_tool_call_loop(self, mock_openai):
        """模拟 tool_call → dispatch → 继续 → 文本回复 的 ReAct 流程。"""
        tc = _make_tool_call("c1", "echo", '{"text":"ping"}')
        msg1 = _make_msg(content=None, tool_calls=[tc])
        msg2 = _make_msg(content="pong")

        mock_openai.chat.completions.create.side_effect = [
            MagicMock(choices=[MagicMock(message=msg1)]),
            MagicMock(choices=[MagicMock(message=msg2)]),
        ]

        agent = AIAgent(api_key="test-key")
        agent.registry = MagicMock()
        agent.registry.dispatch.return_value = "ping"
        agent.registry.get_definitions.return_value = [
            {"function": {"name": "echo", "description": "回显"}}
        ]
        agent.tool_names = {"echo"}
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        reply = agent.run_conversation("echo ping")
        assert reply == "pong"

        # dispatch 被调用一次，参数正确
        agent.registry.dispatch.assert_called_once_with("echo", {"text": "ping"})

    def test_max_iterations(self, mock_openai):
        """LLM 一直返回 tool_call 时触发最大迭代限制。"""
        tc = _make_tool_call("c1", "echo", '{"text":"x"}')
        msg = _make_msg(content=None, tool_calls=[tc])

        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key")
        agent.registry = MagicMock()
        agent.registry.dispatch.return_value = "x"
        agent.registry.get_definitions.return_value = [
            {"function": {"name": "echo", "description": "回显"}}
        ]
        agent.tool_names = {"echo"}
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        reply = agent.run_conversation("start", max_iterations=3)
        assert "最大迭代次数" in reply


class TestConstructor:
    def test_verbose_passthrough(self):
        """verbose 参数传递到 PromptBuilder。"""
        agent = AIAgent(api_key="test-key", verbose=True)
        assert agent.verbose is True
        assert agent.prompt_builder.verbose is True

    def test_default_verbose_off(self):
        agent = AIAgent(api_key="test-key")
        assert agent.verbose is False
        assert agent.prompt_builder.verbose is False

    def test_stream_default_off(self):
        agent = AIAgent(api_key="test-key")
        assert agent.stream is False

    def test_max_retries_default(self):
        agent = AIAgent(api_key="test-key")
        assert agent._max_retries == 3


class TestVisionCapability:
    def test_non_vision_model_rejects_image_block(self, mock_openai):
        """deepseek-chat 不支持 vision，消息含图片时报错。"""
        agent = AIAgent(api_key="test-key", model="deepseek-chat")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        agent.messages.append({
            "role": "user",
            "content": [
                TextBlock(text="看图"),
                ImageBlock(url="https://img.png"),
            ],
        })

        with pytest.raises(ValueError, match="不支持 vision"):
            agent.run_conversation("")

    def test_vision_model_allows_image_block(self, mock_openai):
        """gpt-4o 支持 vision，消息含图片不报错。"""
        msg = MagicMock()
        msg.content = "看到了"
        msg.reasoning_content = None
        msg.tool_calls = None

        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key", model="gpt-4o")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        agent.messages.append({
            "role": "user",
            "content": [
                TextBlock(text="看图"),
                ImageBlock(url="https://img.png"),
            ],
        })

        reply = agent.run_conversation("")
        assert reply == "看到了"

    def test_non_vision_model_text_only_ok(self, mock_openai):
        """纯文本消息，非 vision 模型正常通过。"""
        msg = MagicMock()
        msg.content = "你好"
        msg.reasoning_content = None
        msg.tool_calls = None

        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key", model="deepseek-chat")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        reply = agent.run_conversation("hi")
        assert reply == "你好"

    def test_is_vision_model_helper(self):
        agent = AIAgent(api_key="test-key", model="gpt-4o")
        assert agent._is_vision_model() is True
        agent.model = "deepseek-chat"
        assert agent._is_vision_model() is False
        agent.model = "unknown-model"
        assert agent._is_vision_model() is False


class TestImageInjection:
    def test_extract_screenshot_path_valid(self, tmp_path):
        """有效截图路径被正确提取。"""
        img = tmp_path / "screenshot.png"
        img.write_text("fake-png-data")
        result = f"截图已保存到 {img}（使用 ImageMagick import）"
        extracted = AIAgent._extract_screenshot_path(result)
        assert extracted == str(img)

    def test_extract_screenshot_path_invalid_prefix(self):
        """不是截图结果时返回 None。"""
        assert AIAgent._extract_screenshot_path("some other result") is None

    def test_extract_screenshot_path_file_not_found(self):
        """文件不存在时返回 None。"""
        result = "截图已保存到 /nonexistent/path.png（使用 test）"
        assert AIAgent._extract_screenshot_path(result) is None

    def test_maybe_inject_image_skips_non_screenshot(self, mock_openai):
        """非截图结果不注入图片。"""
        msg = MagicMock()
        msg.content = "ok"
        msg.reasoning_content = None
        msg.tool_calls = None
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        agent.run_conversation("hi")
        # 不应注入 user message
        user_msgs = [m for m in agent.messages if m.get("role") == "user"]
        for msg in user_msgs:
            assert isinstance(msg.get("content"), str)  # 都是纯文本

    def test_maybe_inject_image_screenshot(self, mock_openai, tmp_path):
        """截图工具返回有效路径 → 注入 ImageBlock。"""
        img = tmp_path / "screenshot.png"
        img.write_bytes(b"fake-png")

        tc = MagicMock()
        tc.id = "c1"
        tc.type = "function"
        tc.function.name = "screenshot"
        tc.function.arguments = "{}"
        msg1 = MagicMock()
        msg1.content = None
        msg1.reasoning_content = None
        msg1.tool_calls = [tc]
        msg2 = MagicMock()
        msg2.content = "图片分析结果"
        msg2.reasoning_content = None
        msg2.tool_calls = None

        mock_openai.chat.completions.create.side_effect = [
            MagicMock(choices=[MagicMock(message=msg1)]),
            MagicMock(choices=[MagicMock(message=msg2)]),
        ]

        agent = AIAgent(api_key="test-key", model="gpt-4o")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        # mock dispatch 返回截图路径
        agent.registry.dispatch.return_value = f"截图已保存到 {img}（使用 mock）"

        agent.run_conversation("截图分析")

        # 应有一条 user message 包含 ImageBlock
        user_msgs = [m for m in agent.messages if m.get("role") == "user"]
        assert any(
            isinstance(m.get("content"), list) and
            any(getattr(b, "type", "") == "image_url" for b in m["content"])
            for m in user_msgs
        )


class TestCallWithRetry:
    def test_normal_call_succeeds(self):
        agent = AIAgent(api_key="test-key")
        result = agent._call_with_retry(lambda: "ok", desc="test")
        assert result == "ok"

    def test_rate_limit_retry_then_succeed(self, mock_openai):
        """模拟 RateLimitError 一次后重试成功。"""
        agent = AIAgent(api_key="test-key", max_retries=2)
        calls = []

        def _fn():
            calls.append(1)
            if len(calls) == 1:
                raise openai.RateLimitError("rate limited", response=MagicMock(), body=None)
            return "ok after retry"

        result = agent._call_with_retry(_fn, desc="test")
        assert result == "ok after retry"
        assert len(calls) == 2

    def test_bad_request_not_retried(self):
        """400 错误不重试，直接抛。"""
        agent = AIAgent(api_key="test-key")
        with pytest.raises(RuntimeError, match="请求参数错误"):
            agent._call_with_retry(
                lambda: (_ for _ in ()).throw(openai.BadRequestError("bad req", response=MagicMock(), body=None)),
                desc="test",
            )

    def test_exhaust_retries(self):
        """连续失败达到最大重试次数后抛异常。"""
        agent = AIAgent(api_key="test-key", max_retries=2)
        with pytest.raises(RuntimeError, match="失败"):
            agent._call_with_retry(
                lambda: (_ for _ in ()).throw(openai.RateLimitError("always fail", response=MagicMock(), body=None)),
                desc="test",
            )


class TestToolLoopDetection:
    def test_under_threshold(self):
        agent = AIAgent(api_key="test-key")
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is False
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is False
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is False

    def test_detects_loop(self):
        agent = AIAgent(api_key="test-key")
        agent._detect_tool_loop("echo", '{"text":"hi"}')
        agent._detect_tool_loop("echo", '{"text":"hi"}')
        agent._detect_tool_loop("echo", '{"text":"hi"}')
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is True

    def test_different_args_not_loop(self):
        agent = AIAgent(api_key="test-key")
        for i in range(5):
            assert agent._detect_tool_loop("echo", f'{{"text":"bye_{i}"}}') is False

    def test_reset_on_new_conversation(self, mock_openai):
        """新对话重置循环计数器。"""
        msg = MagicMock()
        msg.content = "ok"
        msg.reasoning_content = None
        msg.tool_calls = None
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        agent.run_conversation("hi")
        # 上一轮如果有工具循环，新对话不应继承
        assert len(agent._tool_call_history) == 0


class TestMaxIterationsWithText:
    def test_returns_generic_message_on_exhaustion(self, mock_openai):
        """只有 tool_call 时达到上限返回通用提示。"""
        tc = MagicMock()
        tc.id = "c1"
        tc.type = "function"
        tc.function.name = "echo"
        tc.function.arguments = '{"text":"x"}'

        msg = MagicMock()
        msg.content = None
        msg.reasoning_content = None
        msg.tool_calls = [tc]

        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )

        agent = AIAgent(api_key="test-key")
        agent.registry = MagicMock()
        agent.registry.dispatch.return_value = "done"
        agent.registry.get_definitions.return_value = [{"function": {"name": "echo"}}]
        agent.tool_names = {"echo"}
        agent.memory = MagicMock()
        agent.memory.get_memory.return_value = ""
        agent.memory.get_user.return_value = ""
        agent.memory.get_episodic.return_value = ""

        reply = agent.run_conversation("start", max_iterations=3)
        assert "最大迭代次数" in reply
        assert "简化请求" in reply


class TestStreaming:
    def test_stream_accumulates_content(self, mock_openai):
        """流式调用正确累积分块内容。"""
        # 构造流式 chunk
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
            chunks.append(chunk)

        # 最后一个 chunk finish_reason=stop
        chunks[-1].choices[0].finish_reason = "stop"

        mock_openai.chat.completions.create.return_value = chunks

        agent = AIAgent(api_key="test-key", stream=True)
        msg = agent._call_llm_streaming({"model": "test", "messages": [{"role": "user", "content": "hi"}]})

        assert msg.content == "Hello World!"
        assert msg.tool_calls is None

    def test_stream_accumulates_tool_calls(self, mock_openai):
        """流式调用正确累积分块的 tool_calls。"""
        chunk1 = MagicMock()
        c1 = MagicMock()
        c1.delta.content = None
        c1.delta.tool_calls = None
        c1.finish_reason = None
        chunk1.choices = [c1]

        # tool_call 分块: 先发 id 和 name
        chunk2 = MagicMock()
        c2 = MagicMock()
        c2.delta.content = None
        tc2 = MagicMock()
        tc2.index = 0
        tc2.id = "call_1"
        tc2.function.name = "echo"
        tc2.function.arguments = ""
        c2.delta.tool_calls = [tc2]
        c2.finish_reason = None
        chunk2.choices = [c2]

        # tool_call 分块: 发 arguments
        chunk3 = MagicMock()
        c3 = MagicMock()
        c3.delta.content = None
        tc3 = MagicMock()
        tc3.index = 0
        tc3.id = ""
        tc3.function.name = ""
        tc3.function.arguments = '{"text":"hello"}'
        c3.delta.tool_calls = [tc3]
        c3.finish_reason = "tool_calls"
        chunk3.choices = [c3]

        mock_openai.chat.completions.create.return_value = [chunk1, chunk2, chunk3]

        agent = AIAgent(api_key="test-key", stream=True)
        msg = agent._call_llm_streaming({"model": "test", "messages": []})

        assert msg.content == ""
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].id == "call_1"
        assert msg.tool_calls[0].function.name == "echo"
        assert msg.tool_calls[0].function.arguments == '{"text":"hello"}'


class TestTrimContext:
    """_maybe_trim_context 的压缩保护和配对完整性。"""

    def test_under_limit_no_trim(self):
        """未超限时不删除任何消息。"""
        agent = AIAgent(api_key="test-key", max_retries=1)
        agent.max_context_chars = 1000
        agent.messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        expected = agent.messages[:]
        agent._maybe_trim_context()
        assert agent.messages == expected

    def test_compress_long_tool(self):
        """tool 结果超限时被截断，消息数量不变。"""
        agent = AIAgent(api_key="test-key")
        agent.max_context_chars = 100
        long_content = "a" * 5000
        agent.messages = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": long_content},
            {"role": "assistant", "content": "done"},
        ]
        agent._maybe_trim_context()
        assert len(agent.messages) == 4
        assert "..." in agent.messages[2]["content"]
        assert len(agent.messages[2]["content"]) < len(long_content)

    def test_remove_middle_group_keeps_first_and_last(self):
        """超限时删除中间组，保护第 1 组和最后 2 组。"""
        agent = AIAgent(api_key="test-key")
        agent.max_context_chars = 200
        # 5 个组：user + 3 轮 assistant+tool + 最终 assistant
        agent.messages = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "x" * 3000},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c2", "content": "y" * 3000},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c3", "content": "z" * 3000},
            {"role": "assistant", "content": "final reply"},
        ]
        agent._maybe_trim_context()
        assert agent.messages[0]["role"] == "user"
        assert agent.messages[-1]["role"] == "assistant"
        assert agent.messages[-1]["content"] == "final reply"
        assert len(agent.messages) < 8
        # 任何剩余的 assistant+tool_calls 都有对应的 tool 跟进
        roles = [m["role"] for m in agent.messages]
        for i, r in enumerate(roles):
            if r == "assistant" and agent.messages[i].get("tool_calls"):
                assert i + 1 < len(roles) and roles[i + 1] == "tool"

    def test_few_messages_no_removal(self):
        """消息数 ≤3 时只压缩，不删除。"""
        agent = AIAgent(api_key="test-key")
        agent.max_context_chars = 50
        agent.messages = [
            {"role": "user", "content": "start" + "x" * 100},
            {"role": "assistant", "content": "hello" * 100},
        ]
        agent._maybe_trim_context()
        assert len(agent.messages) == 2

    def test_preserves_tool_pair_after_trim(self):
        """删除后所有 assistant+tool_calls 都有对应的 tool 跟进。"""
        agent = AIAgent(api_key="test-key")
        agent.max_context_chars = 100
        msgs = [{"role": "user", "content": "go"}]
        for i in range(4):
            msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "x", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 3000})
        msgs.append({"role": "assistant", "content": "over"})
        agent.messages = msgs
        agent._maybe_trim_context()
        for i, m in enumerate(agent.messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                assert i + 1 < len(agent.messages)
                assert agent.messages[i + 1]["role"] == "tool"
                tc_ids = {tc["id"] for tc in m["tool_calls"]}
                tool_id = agent.messages[i + 1]["tool_call_id"]
                assert tool_id in tc_ids, f"tool {tool_id} 没有匹配的 assistant tool_call"
