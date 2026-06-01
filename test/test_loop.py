"""AIAgent 循环测试 — mock OpenAI 客户端，验证 prompt 组装和 ReAct 行为"""

from unittest.mock import MagicMock, patch

import pytest

from agent.loop import AIAgent


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
        agent.memory.get_all.return_value = {
            "memory": "项目记忆",
            "user": "用户偏好",
        }
        agent.context_files = [("/a", "CHIP.md", "# 项目说明")]

        agent.run_conversation("hi")

        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        for layer in ("核心身份", "当前日期", "用户偏好", "记忆快照",
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
        agent.memory.get_all.return_value = {"memory": "", "user": ""}
        agent.context_files = []

        agent.run_conversation("hi")

        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        assert "# 核心身份" in system
        assert "# 当前日期" in system
        assert "# 调用约定" in system
        assert "# 用户偏好" not in system
        assert "# 记忆快照" not in system
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
        agent.memory.get_all.return_value = {"memory": "", "user": ""}

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
        agent.memory.get_all.return_value = {"memory": "", "user": ""}

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
