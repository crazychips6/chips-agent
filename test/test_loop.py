"""AIAgent 循环测试 — mock gateway，验证 prompt 组装和 ReAct 行为"""

from unittest.mock import MagicMock, patch

import pytest

from agent.loop import AIAgent
from agent.message import ImageBlock, TextBlock
from gateway.types import ChatResult


@pytest.fixture
def mock_gateway():
    """提供 mock 的 gateway，避免实际 API 调用。"""
    gw = MagicMock()
    gw.chat.return_value = ChatResult(content="回复")
    return gw


def _make_msg(content: str | None, tool_calls=None, reasoning_content=None):
    """构造模拟的 LLM response message（兼容 ChatResult + SimpleNamespace）。"""
    msg = MagicMock()
    msg.content = content
    msg.reasoning_content = reasoning_content
    msg.tool_calls = tool_calls
    return msg


def _make_tool_call(id: str, name: str, args: str):
    """构造 dict 格式的 tool_call（ChatResult 兼容）。"""
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


class TestBuildAssistantMsg:
    """_build_assistant_msg 将 API 返回转普通 dict。"""

    def test_content_only(self):
        agent = AIAgent()
        msg = _make_msg(content="你好")
        d = agent._build_assistant_msg(msg)
        assert d["role"] == "assistant"
        assert d["content"] == "你好"
        assert "reasoning_content" not in d
        assert "tool_calls" not in d

    def test_reasoning_content(self):
        """DeepSeek reasoning_content 字段被保留。"""
        agent = AIAgent()
        msg = _make_msg(content="最终回答", reasoning_content="思考过程")
        d = agent._build_assistant_msg(msg)
        assert d["reasoning_content"] == "思考过程"

    def test_no_reasoning(self):
        """没有 reasoning_content 时不添加该字段。"""
        agent = AIAgent()
        msg = _make_msg(content="回复")
        d = agent._build_assistant_msg(msg)
        assert "reasoning_content" not in d

    def test_tool_calls_dict(self):
        """ChatResult 格式的 dict tool_calls 正确转换。"""
        agent = AIAgent()
        result = ChatResult(
            content=None,
            tool_calls=[_make_tool_call("call_1", "echo", '{"text":"hello"}')],
        )
        d = agent._build_assistant_msg(result)
        assert d["content"] == ""
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["function"]["name"] == "echo"

    def test_tool_calls_magicmock(self):
        """MagicMock 兼容格式也正常处理。"""
        agent = AIAgent()
        tc = MagicMock()
        tc.id = "call_1"
        tc.type = "function"
        tc.function.name = "echo"
        tc.function.arguments = '{"text":"hello"}'
        msg = _make_msg(content=None, tool_calls=[tc])
        d = agent._build_assistant_msg(msg)
        assert d["content"] == ""
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["function"]["name"] == "echo"


class TestRunConversation:
    """run_conversation 的 prompt 组装与 ReAct 流程。"""

    def test_prompt_has_all_layers(self, mock_gateway):
        """有记忆/上下文/工具时，全部层出现在 system prompt 中。"""
        mock_gateway.chat.return_value = ChatResult(content="回复")

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = [
            {"function": {"name": "echo", "description": "回显"}}
        ]
        agent.tool_names = {"echo"}
        agent.memory_manager = MagicMock()
        agent.memory_manager.build_system_prompt.return_value = "项目记忆\n用户偏好"
        agent.memory_manager.prefetch_all.return_value = ""
        agent.memory_manager.get_all_tool_schemas.return_value = []
        agent.context_files = [("/a", "CHIP.md", "# 项目说明")]

        agent.run_conversation("hi")

        call_kwargs = mock_gateway.chat.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        for layer in ("核心身份", "当前日期", "持久记忆",
                      "项目上下文", "调用约定"):
            assert f"# {layer}" in system, f"缺少层: {layer}"

    def test_prompt_layers_conditional(self, mock_gateway):
        """无记忆、无上下文、无工具时，只有 3 个必现层。"""
        mock_gateway.chat.return_value = ChatResult(content="ok")

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()
        agent.context_files = []

        agent.run_conversation("hi")

        call_kwargs = mock_gateway.chat.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        assert "# 核心身份" in system
        assert "# 当前日期" in system
        assert "# 调用约定" in system
        assert "# 持久记忆" not in system
        assert "# 项目上下文" not in system
        assert "# 工具规则" not in system

    def test_memory_none_no_crash(self, mock_gateway):
        """memory 为 None 时不崩溃。"""
        mock_gateway.chat.return_value = ChatResult(content="ok")

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

        reply = agent.run_conversation("hi")
        assert reply == "ok"

    def test_react_tool_call_loop(self, mock_gateway):
        """模拟 tool_call → dispatch → 继续 → 文本回复 的 ReAct 流程。"""
        mock_gateway.chat.side_effect = [
            ChatResult(content=None, tool_calls=[_make_tool_call("c1", "echo", '{"text":"ping"}')]),
            ChatResult(content="pong"),
        ]

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.dispatch.return_value = "ping"
        agent.registry.get_definitions.return_value = [
            {"function": {"name": "echo", "description": "回显"}}
        ]
        agent.tool_names = {"echo"}

        reply = agent.run_conversation("echo ping")
        assert reply == "pong"

        # dispatch 被调用一次，参数正确
        agent.registry.dispatch.assert_called_once_with("echo", {"text": "ping"})

    def test_max_iterations(self, mock_gateway):
        """LLM 一直返回 tool_call 时触发最大迭代限制。"""
        mock_gateway.chat.return_value = ChatResult(
            content=None,
            tool_calls=[_make_tool_call("c1", "echo", '{"text":"x"}')],
        )

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.dispatch.return_value = "x"
        agent.registry.get_definitions.return_value = [
            {"function": {"name": "echo", "description": "回显"}}
        ]
        agent.tool_names = {"echo"}

        reply = agent.run_conversation("start", max_iterations=3)
        assert "最大迭代次数" in reply


class TestConstructor:
    def test_verbose_passthrough(self):
        """verbose 参数传递到 PromptBuilder。"""
        agent = AIAgent(verbose=True)
        assert agent.verbose is True
        assert agent.prompt_builder.verbose is True

    def test_default_verbose_off(self):
        agent = AIAgent()
        assert agent.verbose is False
        assert agent.prompt_builder.verbose is False

    def test_stream_default_off(self):
        agent = AIAgent()
        assert agent.stream is False

    def test_max_retries_default(self):
        agent = AIAgent()
        assert agent._max_retries == 3

    def test_gateway_injection(self):
        """注入的 gateway 被正确使用。"""
        gw = MagicMock()
        agent = AIAgent(gateway=gw)
        assert agent.gateway is gw


class TestVisionCapability:
    def test_non_vision_model_rejects_image_block(self, mock_gateway):
        """deepseek-chat 不支持 vision，消息含图片时报错。"""
        agent = AIAgent(gateway=mock_gateway, model="deepseek-chat")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

        agent.messages.append({
            "role": "user",
            "content": [
                TextBlock(text="看图"),
                ImageBlock(url="https://img.png"),
            ],
        })

        with pytest.raises(ValueError, match="不支持 vision"):
            agent.run_conversation("")

    def test_vision_model_allows_image_block(self, mock_gateway):
        """gpt-4o 支持 vision，消息含图片不报错。"""
        mock_gateway.chat.return_value = ChatResult(content="看到了")

        agent = AIAgent(gateway=mock_gateway, model="gpt-4o")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

        agent.messages.append({
            "role": "user",
            "content": [
                TextBlock(text="看图"),
                ImageBlock(url="https://img.png"),
            ],
        })

        reply = agent.run_conversation("")
        assert reply == "看到了"

    def test_non_vision_model_text_only_ok(self, mock_gateway):
        """纯文本消息，非 vision 模型正常通过。"""
        mock_gateway.chat.return_value = ChatResult(content="你好")

        agent = AIAgent(gateway=mock_gateway, model="deepseek-chat")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

        reply = agent.run_conversation("hi")
        assert reply == "你好"

    def test_is_vision_model_helper(self):
        agent = AIAgent(model="gpt-4o")
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

    def test_maybe_inject_image_skips_non_screenshot(self, mock_gateway):
        """非截图结果不注入图片。"""
        mock_gateway.chat.return_value = ChatResult(content="ok")

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

        agent.run_conversation("hi")
        # 不应注入 user message
        user_msgs = [m for m in agent.messages if m.get("role") == "user"]
        for msg in user_msgs:
            assert isinstance(msg.get("content"), str)  # 都是纯文本

    def test_maybe_inject_image_screenshot(self, mock_gateway, tmp_path):
        """截图工具返回有效路径 → 注入 ImageBlock。"""
        img = tmp_path / "screenshot.png"
        img.write_bytes(b"fake-png")

        mock_gateway.chat.side_effect = [
            ChatResult(content=None, tool_calls=[_make_tool_call("c1", "screenshot", "{}")]),
            ChatResult(content="图片分析结果"),
        ]

        agent = AIAgent(gateway=mock_gateway, model="gpt-4o")
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

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


class TestToolLoopDetection:
    def test_under_threshold(self):
        agent = AIAgent()
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is False
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is False
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is False

    def test_detects_loop(self):
        agent = AIAgent()
        agent._detect_tool_loop("echo", '{"text":"hi"}')
        agent._detect_tool_loop("echo", '{"text":"hi"}')
        agent._detect_tool_loop("echo", '{"text":"hi"}')
        assert agent._detect_tool_loop("echo", '{"text":"hi"}') is True

    def test_different_args_not_loop(self):
        agent = AIAgent()
        for i in range(5):
            assert agent._detect_tool_loop("echo", f'{{"text":"bye_{i}"}}') is False

    def test_reset_on_new_conversation(self, mock_gateway):
        """新对话重置循环计数器。"""
        mock_gateway.chat.return_value = ChatResult(content="ok")

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.get_definitions.return_value = []
        agent.tool_names = set()

        agent.run_conversation("hi")
        # 上一轮如果有工具循环，新对话不应继承
        assert len(agent._tool_call_history) == 0


class TestMaxIterationsWithText:
    def test_returns_generic_message_on_exhaustion(self, mock_gateway):
        """只有 tool_call 时达到上限返回通用提示。"""
        mock_gateway.chat.return_value = ChatResult(
            content=None,
            tool_calls=[_make_tool_call("c1", "echo", '{"text":"x"}')],
        )

        agent = AIAgent(gateway=mock_gateway)
        agent.registry = MagicMock()
        agent.registry.dispatch.return_value = "done"
        agent.registry.get_definitions.return_value = [{"function": {"name": "echo"}}]
        agent.tool_names = {"echo"}

        reply = agent.run_conversation("start", max_iterations=3)
        assert "最大迭代次数" in reply
        assert "简化请求" in reply


class TestTrimContext:
    """_maybe_trim_context 的压缩保护和配对完整性。"""

    def test_under_limit_no_trim(self):
        """未超限时不删除任何消息。"""
        agent = AIAgent(max_retries=1)
        agent.max_context_chars = 1000
        agent.messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        expected = agent.messages[:]
        agent._maybe_trim_context()
        assert agent.messages == expected

    def test_compress_long_tool(self):
        """tool 结果超限时被截断，消息数量不变。"""
        agent = AIAgent()
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
        agent = AIAgent()
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
        agent = AIAgent()
        agent.max_context_chars = 50
        agent.messages = [
            {"role": "user", "content": "start" + "x" * 100},
            {"role": "assistant", "content": "hello" * 100},
        ]
        agent._maybe_trim_context()
        assert len(agent.messages) == 2

    def test_preserves_tool_pair_after_trim(self):
        """删除后所有 assistant+tool_calls 都有对应的 tool 跟进。"""
        agent = AIAgent()
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
