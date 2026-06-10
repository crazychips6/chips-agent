"""Test delegate_task — 子 Agent 委派

mock gateway 避免真实 API 调用。
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from agent.loop import AIAgent
from gateway.types import ChatResult


@pytest.fixture(autouse=True)
def reset_globals():
    """确保每个测试前 _parent 和 _registry 为 None。"""
    import tool.builtins.agent_tools
    tool.builtins.agent_tools._parent = None
    tool.builtins.agent_tools._registry = None


@pytest.fixture
def mock_gateway():
    """mock gateway，始终返回纯文本回复。"""
    gw = MagicMock()
    gw.chat.return_value = ChatResult(content="子任务已完成")
    return gw


class TestDelegateTask:
    """验证 delegate_task 工具的基本行为。"""

    def _make_parent(self, mock_gateway) -> AIAgent:
        """构造 wiring 好的 parent Agent。"""
        from tool.registry import registry
        agent = AIAgent(model="deepseek-chat", gateway=mock_gateway)
        agent.registry = registry
        # 默认 core toolset
        from tool.toolsets import resolve_multiple_toolsets
        agent.enabled_toolsets = ["core"]
        agent.tool_names = set(resolve_multiple_toolsets(["core"])) & registry.tool_names
        return agent

    def test_wire_and_delegate(self, mock_gateway):
        """基本链路：wire → delegate_task → 返回子 Agent 结果。"""
        from tool.builtins.agent_tools import wire_parent, _handle as delegate

        parent = self._make_parent(mock_gateway)
        wire_parent(parent)

        result = delegate({"task": "帮我查一下今天的天气"})
        assert "子任务已完成" in result

    def test_task_empty(self, mock_gateway):
        """空 task 返回 error。"""
        from tool.builtins.agent_tools import wire_parent, _handle as delegate

        parent = self._make_parent(mock_gateway)
        wire_parent(parent)

        result = json.loads(delegate({"task": ""}))
        assert "error" in result

    def test_with_tools(self, mock_gateway):
        """指定工具集后子 Agent 只加载对应工具。"""
        from tool.builtins.agent_tools import wire_parent, _handle as delegate

        parent = self._make_parent(mock_gateway)
        wire_parent(parent)

        result = delegate({"task": "搜索信息", "tools": ["web"]})
        assert "子任务已完成" in result

    def test_with_model(self, mock_gateway):
        """子 Agent 使用指定模型。"""
        from tool.builtins.agent_tools import wire_parent, _handle as delegate

        parent = self._make_parent(mock_gateway)
        wire_parent(parent)

        result = delegate({"task": "测试", "model": "gpt-4o-mini"})
        assert "子任务已完成" in result

    def test_with_context(self, mock_gateway):
        """额外上下文注入。"""
        from tool.builtins.agent_tools import wire_parent, _handle as delegate

        parent = self._make_parent(mock_gateway)
        wire_parent(parent)

        result = delegate({
            "task": "写报告",
            "context": "用户偏好：喜欢简洁的回复",
        })
        assert "子任务已完成" in result

    def test_sub_agent_isolated_messages(self, mock_gateway):
        """子 Agent 的 messages 不影响父 Agent。"""
        from tool.builtins.agent_tools import wire_parent, _handle as delegate

        parent = self._make_parent(mock_gateway)
        wire_parent(parent)

        # 父 Agent 先有一条消息
        parent.messages.append({"role": "user", "content": "你好"})
        parent._saved_count = 1

        delegate({"task": "子任务"})

        # 父 Agent 的消息历史不变
        assert len(parent.messages) == 1
        assert parent.messages[0]["content"] == "你好"

    @pytest.mark.skip(reason="需要 mock 子 Agent 的 gateway 返回 tool_calls")
    def test_sub_agent_with_tool_calls(self, mock_gateway):
        """子 Agent 使用工具后返回结果。"""
        pass

    def test_not_wired(self):
        """未 wire 时返回 error。"""
        from tool.builtins.agent_tools import _handle as delegate
        result = delegate({"task": "测试"})
        assert "not initialized" in result


class TestWireParent:
    """验证 wiring 机制。"""

    def test_wire_replaces_global(self):
        import tool.builtins.agent_tools as at
        assert at._parent is None  # 未 wire

        agent = AIAgent()
        at.wire_parent(agent)
        assert at._parent is agent


class TestAgentRegistry:
    """验证 Phase 2 — Agent Registry 集成。"""

    @pytest.fixture
    def mock_registry(self):
        """模拟 AgentRegistry，不依赖文件系统。"""
        from unittest.mock import MagicMock
        reg = MagicMock()
        reg.get.return_value = {
            "model": "deepseek-chat",
            "tools": ["web"],
            "max_iterations": 20,
            "system_prompt": "你是研究助手",
            "description": "研究助手",
        }
        reg.names = ["researcher"]
        return reg

    def test_registry_agent_resolved(self, mock_gateway, mock_registry):
        """注册表 Agent 正确解析参数。"""
        import tool.builtins.agent_tools as at

        parent = self._make_parent(mock_gateway)
        at.wire_parent(parent)
        at.wire_registry(mock_registry)

        result = at._handle({"agent": "researcher", "task": "搜索"})
        assert "子任务已完成" in result
        # 验证 registry.get 被调用
        mock_registry.get.assert_called_once_with("researcher")

    def test_registry_unknown_agent(self, mock_gateway, mock_registry):
        """未知 Agent 名返回 error + 可用列表。"""
        import tool.builtins.agent_tools as at

        mock_registry.get.return_value = None
        mock_registry.names = ["researcher", "coder"]

        parent = self._make_parent(mock_gateway)
        at.wire_parent(parent)
        at.wire_registry(mock_registry)

        result = json.loads(at._handle({"agent": "hacker", "task": "测试"}))
        assert "error" in result
        assert "hacker" in result["error"]
        assert "researcher" in result["error"]
        assert "coder" in result["error"]

    def test_registry_not_wired(self, mock_gateway):
        """未 wire registry 时使用 agent 参数返回 error。"""
        import tool.builtins.agent_tools as at

        parent = self._make_parent(mock_gateway)
        at.wire_parent(parent)
        # 故意不 wire registry

        result = json.loads(at._handle({"agent": "researcher", "task": "测试"}))
        assert "error" in result
        assert "未初始化" in result["error"]

    def test_registry_agent_with_override(self, mock_gateway, mock_registry):
        """注册表 Agent 允许内联参数覆盖。"""
        import tool.builtins.agent_tools as at

        mock_registry.get.return_value = {
            "model": "deepseek-chat",
            "tools": ["web"],
            "max_iterations": 20,
            "system_prompt": "默认提示",
            "description": "研究助手",
        }

        parent = self._make_parent(mock_gateway)
        at.wire_parent(parent)
        at.wire_registry(mock_registry)

        result = at._handle({
            "agent": "researcher",
            "task": "任务",
            "model": "gpt-4o",
        })
        assert "子任务已完成" in result

    def test_wire_registry_global(self):
        """wire_registry 正确设置全局。"""
        import tool.builtins.agent_tools as at
        assert at._registry is None

        reg = MagicMock()
        at.wire_registry(reg)
        assert at._registry is reg

    def _make_parent(self, mock_gateway) -> AIAgent:
        """构造 wiring 好的 parent Agent。"""
        from tool.registry import registry
        agent = AIAgent(model="deepseek-chat", gateway=mock_gateway)
        agent.registry = registry
        from tool.toolsets import resolve_multiple_toolsets
        agent.enabled_toolsets = ["core"]
        agent.tool_names = set(resolve_multiple_toolsets(["core"])) & registry.tool_names
        return agent
